# 迭代记录 · 2026-10-17 主机续训链：两个源码缺陷（A84/A85）与一个待定口径

> 状态：源码已落并推 `gitee-exp`；`github-exp` 当日 443 不通，**待补推**（双备份未完整）。
> 提交链：`a65eac2`(A84) → `2ebbf12`(A84 同族收口) → `15fbb3a`(A85)。
> 验收：`manage.py test --stage 10` **18 套全绿**；`python -m tools.check` 错误 **4009**、警告 **0**；
> 新增契约 `tools/test/test_state_geometry_contract.py` **10/10**。

本机续训链的当前读数（同种子 20261011、adamw、wikitext-2 train；留出集 wikitext-2 validation）
  step 75  seq32  CE 9.1362 | step100 seq32 CE 8.8883 | step125 seq32 CE 8.8448
  step150  seq32  CE 8.8707（回升 +0.0259）
  step175  seq128 CE 8.8208 | step200 seq128 CE 8.8004（最低，续降 -0.0204）
  step225  seq256 CE 8.8194（比 seq128 高 0.0190，与窗口间波动同量级 ⇒ 杠杆不单调）
  均匀基线恒为 11.9293；每点 `beats_uniform=true`、`verdict=true`、`changed=512/578`
证据检查点：E:/pisceslx-artifacts/ckpt/keep_step{100,125,150,175_seq128,200_seq128,225_seq256}.pt

下一条（唯一需要你先定口径的）：MoE 辅助路由头的 warmup 阈值按主机档位降档

已修的部分（A85，`15fbb3a`）：
  - `model/moe/gate.py` 用模块私有 `current_step` 门控 HiCL / GOT / SoftMoE 三个辅助路由头；
    该计数器不是 buffer、不进检查点，每进程归零，且门闩写成 `== warmup_steps`。
    两层叠加 ⇒ 在任何 `--resume_ckpt` 链上 `w_hicl|w_got` 与两个 `sparse_gate_*` 永无梯度。
  - 修法：`YvModel.adopt_training_progress(global_step)` 在 `load_checkpoint` 恢复步数后下发
    权威进度（只前进）；门闩改 `>=` 单调形式。
  - 实测（同一 17.20M 真模型）：`w_hicl.grad` None → -2.93e-05 / 6.45e-06；
    `w_got.grad` None → -9.55e-05 / -4.85e-04；`_routing_heads_enabled` False → True。

**为什么还没改善指标**：阈值本身不可达 —— 实测 0.01B/主机档
`moe_random_to_gradient_steps` 默认 **2000**、路由头 warmup 亦为 **2000**，
而本机链条现在 **225 步**（每窗口 25-30 步、约 10 s/步）。
`adopt(225)` 仍低于 2000，所以未更新张量数**不变（仍 66）**。
这不是缺陷未修，是"机制已通、工况未到"。

两个候选口径（都要改档位契约，不是改一个数）：
  A. 按主机档降 warmup：在 `configs/host/cpu.yaml` 声明 `routing_head_warmup_steps` 之类的
     档位量（cpu 档取 ~150，turing/accelerated 保持 2000），让主机链条真能激活路由头，
     代价是主机档学到的路由头来自更短的预热期 —— 读数只用于本机档位。
  B. 不动 warmup，接受主机档这 4 个头不训练，改在 T4/启智跑到 2000 步后再验。
     代价是 `changed=512/578` 在本机永远缺这 4 项，且无法在本机证"被激活知识块参与训练"这一类主张。
  我倾向 A 并在证明里写清"预热期与模型规模无关的假设"，但这是**你定的口径**，不替你决定。

复现命令（本机、E 盘工作根）：
  PISCESLX_HOME=E:/pisceslx-artifacts OMP_NUM_THREADS=4 python -u manage.py train \
      --train_config .pisceslx/ab/win7_seq256.yaml --model_size 0.1B \
      --dataset data/corpus/wikitext__wikitext-2-raw-v1_train-00000-of-00001.jsonl \
      --resume_ckpt E:/pisceslx-artifacts/ckpt/keep_step225_seq256.pt
  python tools/dev/verify_checkpoint.py --checkpoint E:/pisceslx-artifacts/ckpt/<ckpt>.pt \
      --model_config configs/model/0.1B.yaml \
      --corpus data/corpus/wikitext__wikitext-2-raw-v1_validation-00000-of-00001.jsonl
注意：续训旗标是 `--model_size 0.1B`（hidden 256，578 张量）；用 `0.01B` 会被几何闸门拒绝并退出 1。

## A84 一句话结论

恢复权重的 7 处 `strict=False` 全部过 `utils/state_geometry.py::PiscesLxStateGeometryGate`
（形状不符 ⇒ 整份拒绝，一条消息点名不符数量与前 8 个键），
含**推理引擎两处**（半加载不报错、只出错答案）；全仓扫描 + 契约测试防新恢复点绕过，
错误档位实测退出码 1、点名 `448 of 578 tensor(s) do not fit`。

## 本轮教训（可复用）

1. **量退出码不要接管道**：我曾把 `cmd | grep` 的状态当成训练退出码，误判又撞上 A24。
2. **latch 类状态要在触发它的那次前向之后再读**：我曾把 `_routing_heads_enabled` 打印在前向之前，
   误判修复无效。
3. **单窗口读数不能定论**：seq 128 的第一次改善只有一个点，第二个同向窗口才算证据；
   而 seq 256 立刻把"越长越好"证伪。
4. **机制修好 ≠ 指标改善**：A85 的梯度实测通了，但阈值 2000 > 本机 225 步，
   未更新张量数不变；这句话要一起交付，不能只给前半句。
