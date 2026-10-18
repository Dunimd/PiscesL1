# 交接提示词 · PiscesL1（2026-10-18）

> 把本文件当作你接手的**第一份指令**。它不替代 `AGENTS.md` 与 `docs/prompts/UPGRADE_*.md`，
> 而是告诉你：仓库怎么拿、先看什么、现在到哪一步、剩下哪些活以及每项的验收判据。

---

## 0. 一句话任务

把 PiscesL1 升级为 2026 年最先进的多模态稀疏专家 MoE 框架，并**交付一个真实可用的模型权重**。
四条铁律（先查资料别空想 / 真实可证明是一个整体 / 权重上 HF+ModelScope / 诚实优先于达标）
不可协商，详见 `AGENTS.md` §2 与 `docs/prompts/UPGRADE_00_INDEX.md`。

---

## 1. 从 Git 拿到仓库

```bash
# 主仓库（源码，不含任何权重）
git clone https://github.com/Dunimd/PiscesL1.git        # 或
git clone https://gitee.com/dunimd/piscesl1.git
cd PiscesL1 && git checkout master

# 日常迭代仓库（本会话的所有提交先落在这里，再同步到主仓库）
git clone https://github.com/Dunimd-Experimental/PiscesLx.git
```

核查过的事实：**仓库里没有任何权重被跟踪**（`git ls-files` 无 `.pt/.safetensors/.bin/.ckpt`），
最大跟踪文件是 `tokenizer/tokenizer.json`（9.6 MB，属词表工件，不是权重）。
权重产物一律在训练机的 `PISCESLX_HOME`（本机为 `E:/pisceslx-artifacts`），**禁止提交进仓库**。

远端命名约定（本地 `.git/config` 已配好，四个都在）：

| 远端 | 用途 |
|---|---|
| `gitee-exp` / `github-exp` | 实验性仓库，日常迭代默认推送目标（`remote.pushdefault`） |
| `gitee` / `github` | 主仓库，需显式写远端名才推：`git push gitee master` / `git push github master` |

推送纪律：**源码全量、权重零提交**；重要变更 commit 后推 `gitee-exp`，里程碑时同步主仓库；
提交信息必须带实测证据（数字、退出码、文件:行号）。

---

## 2. 阅读顺序（别跳）

1. `AGENTS.md` —— 操作性契约：角色、铁律、编码规范（`Yv*`/`PiscesLx*` 命名、禁止模块顶层裸函数、
   dmsc 日志、版权头逐字、禁硬编码）、禁令 24 条、验收口径。
2. `docs/prompts/UPGRADE_01..10` —— 指令书原文（冲突时以它为准）。重点：
   `03_ARCH`（模态=4、查算分离、思考）、`06_CODING`、`07_T4`、`09_METRICS`、`10_DELIVER`。
3. `docs/audit/00-audit-report.md` —— **本仓库的真相底稿**，按节号递增。接手前至少读
   §107（配置到不了门）、§108-§110（A98 取证与撤回）、§111-§119（分词器→标记→几何）、
   §120-§125（思考流闭环、资源边界）。里面有多次**我自己被实测否证的记录**，照它走能少走弯路。
4. `docs/iterations/` —— 过程留痕；`2026-10-18-a99-tokenizer-fragmentation.md` 是分词器/标记专题。
5. `docs/design/` + `docs/proofs/` —— 机制设计与数学证明（RPI/反正弦序律、直通门、PathMoE、
   低秩共振路由）。**创新必须配证明**，实现与证明一一对应。
6. `tools/check/` —— 禁令→可机检。跑 `python -m tools.check`。

---

## 3. 环境（本机与训练机不同，别照抄）

* 本地：Windows，Python 3.14 + CPU torch。**`python3` 是坏别名（静默 exit 49），一律用 `python`**。
* 产物目录：`PISCESLX_HOME=E:/pisceslx-artifacts`（日志在 `$PISCESLX_HOME/logs/<命名空间>/output.log`）。
* 本机硬闸约 **4.6 GB**：**带 span 的 0.1B 链在本机不可行**（`chainA`/`chainB` 均
  `CHAIN_EXIT=139`、零 step、`ckpt/` 空，见 §125）。0.1B/7B 级训练请上 **T4（Kaggle）/ 启智 AIStudio**。
* Kaggle 限制：无法普通 shell 交互，系统命令要 `!` 前缀或 `os.system/subprocess`；一切封装成可断点续跑的
  Python 批处理 + 后台日志；非计算任务释放 GPU。凭据/账号见 `AGENTS.md` §13（**不要把明文写进日志、
  提交信息或本文档**）。

---

## 4. 验收命令（任何"做完了"都要过这三关）

```bash
python -m tools.check                       # 禁止项机检；当前基线 errors 4094 / warnings 0
python manage.py test --stage 10            # 逐个子进程跑 tools/test/test_*.py；当前 20/20
python tools/dev/verify_checkpoint.py \
  --checkpoint <ckpt.pt> --model_config configs/model/0.1B.yaml \
  --corpus data/corpus/wikitext__wikitext-2-raw-v1_validation-00000-of-00001.jsonl
```

`verify_checkpoint` 回答三件事：能否 `strict` 载入 / 与同配置新建模型逐参数比对是否**真的被更新** /
真实留出文本上的困惑度。**没训过的 `final_model.pt` 必然过不了**，这正是它存在的意义。

**读数纪律**（都踩过坑，写死在 §121-§125）：

1. 退出码不要用 `cmd | grep` 的尾部，用 `${PIPESTATUS[0]}`；进度条刷新行的数字**不是最终值**
   （我曾把 `Summary: 19/19` 当成交付，最终是 20/20）。
2. 两次窗口比对权重前，先确认 `global_step` **真的前进**（我曾用 `--max_steps 2` 续 `step=2` 的点了
   0 步的窗口，得出"权重不更新"的错误结论）。
3. 组件日志在 `logs/` 里，不在 stdout；文件含 ANSI/二进制，grep 前 `tr -cd '\11\12\40-\176'`。
4. 判"某机制不生效"前先钉住同子系统的**缓存、门控阈值、自适应形状**。
5. **通路要在最外层入口复验**（CLI/服务/模型 forward），子系统内通了不算通。
6. 静态检查的豁免**只认同行注释或具名常量**，写在上面的说明注释不生效。

---

## 5. 已完成（有数字，可直接引用）

* **A98 思考流监督**：通路七段全部实测打通——工件 `special` 标志修正 → `encode(标记)` 成为单 id；
  标记解析后端权威（`get_special_token_id`）；批次含闭合区间（真实数据集 **40/40**）；
  reasoner 在训练中物化；区间**成对闭区间**语义（未闭合不监督，`tools/test/test_reasoning_span_supervision.py`
  8/8 含阳性对照）；惰性参数进优化器（A103：组数 1→2、Adam 条目 437）；
  **真实两次窗口 `thinking_head delta_norm = 0.002149`**。
* **A100 重复头**：CoT 支不再有第二个 `hidden×vocab` 头（消除 **310,722,654** 参数），
  进一步**共享 `lm_head`**（保存状态里两键 `torch.equal == True`，`delta 0.007945`）。
* **A102 词表几何**：12 个预设 `vocab_size 151646 → 154885`。旧几何下 **65 个控制标记全被 clamp
  折叠到同一行**——这是模态/工具/思考能力一直起不来的共同底因。
* **A105 续跑**：优化器状态改为**前缀恢复**（此前 2 组续 1 组会丢掉全部 437 条动量）。
* **A94 工具方言**：`tools/infer/core.py` 与 `agent_interceptor.py` 的 7 条 XML 正则删除，
  检测与执行统一走 `model/multimodal/tool_call_codec.py`（`tools/test/test_served_tool_calls.py` 6/6）。
* **A101 / A99(a,b) / §107**：CRV 与 reasoner 开关解耦、开关单口径；标记解析后端权威、禁止碎片 id 冒充
  词元；配置别名除根（同检查点重跑证明数值等价，CE 逐位不变）。
* 闸口基线：`stage-10 = 20/20`、`tools/check = 4094 errors / 0 warnings`。

---

## 6. 剩下的活（按优先级，每项都给了判据）

### T1（最高）0.1B 起带 span 的真实链 —— 需 T4/启智
判据：`verify_checkpoint` 三项全过（可载入 / **参数确实被更新** / 留出集 CE 优于均匀基线），
且 `thinking_head.weight` 的两次窗口 `delta_norm > 0`。
判读按 `docs/audit/00 §123`：**带 span 的训练 loss（含 reasoner 项，量级 ~33）与纯文本留出集 CE
是两个量，禁止互减**；进步需三点均值 + 极差（历史窗口噪声 ±0.12 nats）。
注意：旧链 `E:/pisceslx-artifacts/retired-151646-geometry/` 已退役，**A84 几何闸会正确拒绝**它们，
不要绕过、不要逐键删除后 `strict` 载入。

### T2 阶段 4：三模态真实数据（image / audio / video）
现状：`data/` 只有文本（wikitext + gsm8k 派生）。管线判定见 `utils/modality_scanner.py::scan_dataset`
（先看 HF schema 的 `column_names/features`，否则抽样看 batch 的 key）；
`tools/train/orchestrator.py:1220` 打 "text-only, skipping multimodal encoders" 就是因为数据没有非文本键。
判据：造出的语料被扫描器识别出 `{'text','image',...}`，且对应编码器在训练中被真正调用
（不要只看"数据能加载"）。禁止低质/重复/有害语料；数据集自采自清洗，产物按仓库约定不进 git。

### T3 reasoner 常驻参数降阶（`§9 又加又减`）
动机不是好看，是 §125 的资源边界：0.1B 一物化 reasoner 就越过 4.6GB。把三条流的投影改低秩
（`H→r→H`）或复用主干隐态，常驻参数 `O(H·V + 3H²) → O(H·r)`。
**需先出设计与数学证明**（`docs/design/` + `docs/proofs/`，表达能力不弱于被替代机制、给出误差界），
再改代码；改完重跑第 4 节三关并在 0.1B 上验证 A98 档位判据。

### T4 死声明除根（防复活）
`model/tokenizer/special_tokens.py` 里 **17 个零消费者字段**（含 `document_token`，属"document 不再是
独立模态"的禁令 #18 残留）与 `tokenizer.json` 中 **15 条 `special=false` 且代码零消费**的控制串
（`id 154838..154855`：`code_prefix/middle/suffix`、`tool_call/tool_response/arg_*`、`/nothink`）。
处置是**删声明**而不是把标志翻成 true（见 §124 附）。判据：删除后 `python -m tools.check` 与
`stage-10` 全绿，且新增一条机检：**声明的标记串必须等于词表实际注册的串**（阳性对照：故意改坏一条必失败）。

### T5 clamp 不再静默
`tools/train/core.py` 里对 `input_ids` 的 `clamp(0, vocab-1)` 正是掩盖 A102 的元凶。
改成越界**计数并 WARNING 点名**，并加契约测试：**每个 `configs/model/*.yaml` 的
`vocab_size` 必须 ≥ `YvTokenizer().vocab_size`**。

### T6 权重交付与同步
真权重产出后：HF + ModelScope 双上传（每个 checkpoint 产生即传），源码全量备份 Gitee + GitHub
（**不含权重**），并把 §13 的验收清单逐项打勾。静态存量 `4094`（`NO-MAGIC-NUMBER`/`NO-SILENT-EXCEPT`）
随批清理，别为它单开一轮。

---

## 7. 工作方式（用户的硬要求，会当场纠正你）

* **一次做好、不留兼容层**；不要"精简算法"；改动要全项目同步（配置/CLI/工具链/数据/服务/评测/文档/测试），
  不留孤儿 import、不新旧并存。
* **每轮必须有源码 diff**。只改 markdown 或只写测试会被质问；创新与修旧账**同时推进**，
  但**不要为"修旧账占比"自我检讨**——那些乱账本来就在任务范围内。
* **技术取舍自己定并落账**（包括动 embedding 几何这类），**不要拿这类分叉回头问他**。
  可以问的只有"要占用他的资源/时间"的事（下载几十 GB、占他的 GPU 窗口）。
* 按**里程碑**提交，不是做完一件事就提交；提交信息写证据；**未验证的东西不进提交**——
  预算不够就登记精确 file:line 取证 + 可执行修复口径，并**如实收窄此前结论的边界**。
* 长跑纪律：不并发 GB 级进程（曾两次顶死训练）；跑测试期间不改源码；对照实验先隔离产物路径；
  临时探针同轮删净。确认缺陷使旧权重/基线失效就**主动退役**（移动 + README 禁复用），
  但区分"仍然有效的读数"与"不能再当对照的部分"。
* 用**中文**回复。别拿"你没重启/这是旧进程/基线本来就红"当解释——先拿数据证明代码没问题。

---

## 8. 代码地图（接手最容易用到的坐标）

| 主题 | 位置 |
|---|---|
| 工具调用方言（唯一事实来源） | `model/multimodal/tool_call_codec.py`（`parse/render/render_result`） |
| 服务侧执行 / 拦截 | `tools/infer/core.py::_process_tool_calls`、`tools/infer/agent_interceptor.py::intercept` |
| 思考区间监督 | `model/reasoning/reasoner/multipath_core.py`（`_STREAM_MARKERS`、`reasoning_intervals`、`_create_reasoning_masks`、`has_reasoning_span`） |
| 标记解析 | `model/tokenizer/tokenizer.py::resolve_token_id`（后端权威）+ `model/tokenizer/special_tokens.py` |
| 模型的 span 门控 | `model/core/model.py`（`bind_tokenizer`、`_batch_has_reasoning_span`、`_reasoning_marker_ids`、`_lazy_get_reasoner` 内的 `lm_head` 绑定） |
| 惰性参数进优化器 / 续跑恢复 | `tools/train/core.py`（`_adopt_lazy_parameters`、`load_checkpoint` 的前缀恢复） |
| 检查点几何闸 | `utils/state_geometry.py`（A84），7 处 `strict=False` 恢复全部经它 |
| 数据集与 span 语料生成 | `tools/data/dataset/raw_text.py`（`INSTRUCTION_PAIRS`）、`tools/data/tool_trace.py`（逐步骤 `THINK_OPEN/THINK_CLOSE` 闭区间） |
| 禁令机检 | `tools/check/`（`python -m tools.check`，`--quiet/--json`） |

---

## 9. 接手第一步（建议顺序）

```bash
git clone <主仓库或实验仓库> && cd PiscesL1
python -m tools.check                 # 基线应为 errors 4094 / warnings 0
python manage.py test --stage 10      # 基线应为 20/20
git log --oneline -20                 # 最近里程碑：§122 A105、§123 规程、§124 共享 lm_head、§125 资源边界
sed -n '1,60p' docs/audit/00-audit-report.md   # 底稿开头，然后直接跳 §107 起
```

然后从 **T1（有算力）/ T5（纯本地可做完）/ T4（纯本地可做完）** 里挑一条开工，每完成一项就更新
`docs/audit/00-audit-report.md` 的节号并推 `gitee-exp`。

**没有真实权重之前，任务不算完成；任何绕过检查的"绿灯"都等于造假。**
