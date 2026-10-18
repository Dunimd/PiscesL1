# A99：分词器把声明的特殊标记碎化，并把"第一个碎片"的 id 当作词元 id 静默存下

日期：2026-10-18（本机 0.1B 档，全部为实测，非读码推断）

## 结论

A98（思考流监督通路断开，见 `docs/audit/00-audit-report.md` §108/§110）的根因**比 §110 记的四点更靠前一层**：
项目分词器在运行时**不能把声明的特殊标记当作一个词元**，而 `_prepare_special_tokens` 取 id 的方式
又不校验"是否只有一个词元"，于是把碎片的第一个 id 当词元 id 存下来。两件事叠加，使任何
"标记条件化"的能力（思考 span、模态标记、工具标记）在**数据面上根本不可表达**。

## 实测证据

1. 工件层：`tokenizer/tokenizer.json` 的 `added_tokens` 共 **36** 条，id 区间 **154820..154855**，
   正好从后端 `vocab_size = 154820` 起排；模型侧 `cfg.vocab_size = 154885`，即 embedding 已为这些 id 预留行。
2. 运行时层：把 `tokenizer.json` 里登记的 think 起止标记（竖线加尖括号形式）交给分词器编码，
   实测得到 `[27, 91, 26779, 91, 29]`（5 个碎片）与 `[27, 91, 14, 26779, 91, 29]`（6 个碎片）。
   后端 `vocab_size` 仍是 **154820**：这些 added tokens **从未注册进运行时**，只在文件里声明。
3. 静默错值：`model/tokenizer/tokenizer.py:291-300` 用 `encode(token)[0]` 取 id。
   实测 `tokenizer_config.json` 的 `extra_special_tokens`（47 条）里有 **26 条**是同样碎化的，
   于是 `_multimodal_token_ids` 里存的是碎片 id（例如 think 起始标记存成 **27**），且不报错。
4. 反查失败：`get_special_token_id()` 对 think 起始标记返回 **None**；
   另测 `YvSpecialTokens` 声明的 answer / reasoning 一族标记，**没有一个**在 `added_tokens` 里，
   且 `reasoning_start` / `reasoning_end` / `document_token` 等 **17 个字段全仓零消费者**
   （`document_token` 同时是"document 已不是独立模态"的残留，属禁令 #18 未除根）。
5. 修复尝试：在 `_prepare_special_tokens` 前插入"按 `tokenizer.json` 的声明补注册 + 校验 id 必须等于
   文件里的 id + 禁止碎片 id"的实现，跑通失败——该 transformers 版本的
   `add_tokens` 只接受 `List[str]` 或 `AddedToken`，传字典条目直接
   `TypeError: Input must be a List[Union[str, AddedToken]]`（栈：`tokenization_utils_tokenizers.py:728`）。
   改动已 `git checkout` 回退，**树上不留未验证的分词器改动**。

## 本轮已完成的一处对齐（低风险）

`model/tokenizer/special_tokens.py:198-199` 的 think 起止声明由"缺闭合角括号"的形态改为
`added_tokens` 里**实际注册**的那一对（id 154841 / 154842），并去掉两处 docstring 里重复的默认值字面量。
注意：这**不解除 A99**——运行时仍会把它们碎化；收益是声明与工件不再是两个分家的事实来源，
`unified.py:628` 那种"在解码文本里子串匹配结束标记"的判据从此对得上真实串。

## A99 的执行顺序（下一轮按此做）

1. 让 `YvTokenizer` 在加载后把 `tokenizer.json` 的 `added_tokens` **真正注册**进后端：
   以 `[str]` 形式按 id 升序调用 `add_tokens(..., special_tokens=True)`（不要用字典条目，见证据 5），
   注册后**逐条断言**"运行时解析到的 id == 文件声明的 id"，不一致即 `RuntimeError`——
   否则 embedding 行与标记寻址的行会错位。
2. `_prepare_special_tokens` 改为**只接受单词元解析**（`encode(token)` 长度必须为 1），
   拿不到就抛错并点名标记；**禁止**再写"碎片首 id"这种静默错值。
3. 删除 17 个零消费者的标记声明（含 `document_token`，同时清掉禁令 #18 残留）及其 docstring，
   不保留"以后可能用"的声明。
4. 机检与阳性对照：`tools/test/` 新增契约测试，断言 (i) 声明为标记的每个串在运行时解析为**一个** id；
   (ii) 该 id 与 `tokenizer.json` 声明一致；(iii) 故意把某条声明改坏时测试必须失败。
5. 等价性证明（改分词器必做）：用 `python tools/dev/verify_checkpoint.py`
   对 `E:/pisceslx-artifacts/ckpt/keep_step375_seq256_a95.pt` 复跑，留出集 CE 必须仍是 **8.9261**
   （现行语料不含标记串，注册 added tokens 不应改变普通文本的分词）；
   再加 `python manage.py test --stage 10` 全绿，才谈得上继续 §110 的 (b)-(g)。
6. 数据侧（M4）随后才能产出可被监督的 thinking span，A98 的判据 `thinking_head.grad is not None` 才有意义。

## 与其他在册缺陷的关系

- A98（思考 span 监督断开）被本条阻塞在最外层；§108/§110 的四点闭合仍然有效，但要在 A99 修好后才可达。
- A94（服务侧仍按 XML 解析工具调用）与本条无关，仍在册。

## 追加（同日）：第 1 步的假设被实测否证——运行时补注册会**换行**，不能那样修

按上面第 1 步做了一次一次性实验（`AutoTokenizer.from_pretrained('tokenizer', local_files_only=True)`，
只测不改源码），三条实测：

1. 后端 `vocab_size = 154820`，且 think 起始标记**不在** `tokenizer.vocab` 里
   （这更正了本文早先"`added_tokens` 的 36 条在 `.vocab` 里查得到、只是不参与切分"的推断——
   前一次补丁没打印注册日志、也没抛 `TypeError`，真正原因是我当时用了**字典条目**形式，
   该版本的 `add_tokens` 在 `tokenization_utils_tokenizers.py:728` 只收 `List[str]/AddedToken`，
   而我的分支逻辑先以"是否已存在于 `.vocab`"判定缺失，判据本身就是错的）。
2. 用 `[str]` 形式注册有效：`t.add_tokens([think_start], special_tokens=True)` 返回 1，
   之后 `encode` 从 5 个碎片变成**一个** id；`convert_tokens_to_ids` 同步可查。
3. **但分到的 id 是 154885，而工件声明的是 154841。** 154885 = 154820 + 65，
   65 正好是 `tokenizer_config.json` 的 47 条 `extra_special_tokens` 与 `tokenizer.json` 的
   36 条 `added_tokens` 的**并集大小**（交集实测 18）。也就是说后端把两份名单的并集排在
   154820 之后自增发号，与 `tokenizer.json` 里**逐条写死的 id 不是同一套坐标**。

**结论（推翻本文第 1 步）**：运行时"补注册 + 断言 id 相等"这条路走不通——断言必然失败；
若改成"注册后把 embedding 也 resize 到新 id"，则是在给既有 checkpoint 换行，
会直接冲击已交付链上 `keep_step375_seq256_a95.pt` 的几何一致性（A84 几何闸会拒或更糟：静默错位）。

**因此 A99 的正确战场是工件本身，不是运行时补丁**：必须让
`tokenizer/tokenizer.json` 的 `added_tokens` 与 `tokenizer/tokenizer_config.json` 的
`extra_special_tokens` **合并成一套自洽坐标**（同一串、同一 id、顺序与模型 embedding 行数一致），
并且 `cfg.vocab_size` 由这套坐标**推导**而不是另写。这是一个"数据工件 + 配置"的重建任务，
必须配套机检：逐条断言"串 → 运行时 id == 工件声明 id == embedding 行号"，
阳性对照用故意把某条 id 改错。改完仍须按本文第 5 步用 `verify_checkpoint` 证明
留出集 CE 仍为 8.9261（现行语料不含标记串，普通文本的分词不应受影响）。

本轮**未改任何源码**（实验是一次性脚本，树上无残留），源码净变化仍为零。

## 再追加：两套坐标的**数量账**已经对上，正确修法因此唯一确定

实测四个数（一次读工件，无源码改动）：

```
added_tokens            36 条   声明 id 区间 154820..154855
extra_special_tokens    47 条
交集                    18 条
并集                    65 条     → 154820 + 65 = 154885 = YvTokenizer.vocab_size = 模型 embedding 行数
```

这解释了上面第 3 条：后端把**两份名单的并集**自 154820 起顺序发号，一直发到 154884；
而 `tokenizer.json` 里逐条写死的 154820..154855 是**另一套坐标**，与前者**互相冲突**
（同一 id 被两套语义占用）。因此：

- **模型的 embedding 行是按并集坐标建的**（154885 行），不是按 `added_tokens` 的声明 id 建的；
- 上面实验里补注册拿到 154885，是并集之外**再发一个新号**，即**落在不存在的行上**（合法行号 0..154884）——
  这正是"运行时补丁"绝不能上生产的原因；
- 唯一自洽的修法是**把工件合成一套坐标**：以并集为准重建
  `tokenizer.json` 的 `added_tokens` 与 `tokenizer_config.json` 的 `extra_special_tokens`
  （同一串、同一 id、id 必须 < `cfg.vocab_size`），`cfg.vocab_size` 由这套坐标推导而非另写常量。
- 机检随做：逐条断言"串 → 运行时 id == 工件声明 id < embedding 行数"，
  并保留阳性对照（故意把某条 id 改到行号之外或与之冲突，测试必须失败）。
- 改后判据不变：`verify_checkpoint` 对 `keep_step375_seq256_a95.pt` 的留出集 CE 仍须是 **8.9261**，
  且普通文本的分词逐 id 不变（现行语料不含标记串，重建坐标不应扰动既有 token 的 id）。

**为什么这必须做在工件层**：A98/A94 与全部标记条件化能力（思考 span、模态标记、工具标记）
都依赖"标记是一个词元且指向确定的 embedding 行"。坐标不统一时，
`thinking_head` 的 44% 参数、服务侧的工具执行、数据侧的 span 监督**都无从谈起**——
这就是本会话从 §108 一路挖到这里的因果链底部。

