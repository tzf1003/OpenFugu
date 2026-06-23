# OpenFugu 后台控制台设计方案

## 目标

OpenFugu 控制台不是通用 CMS。它要解决三件事：

1. 看清当前服务、模型、worker、路由和实验结果。
2. 管理同一个基础模型的多个 API 接入源，在成本、稳定性、质量之间切换。
3. 新模型发布后，能跑 profiling、训练/评估分类器 head，再决定是否上线。

控制台覆盖完整运维闭环：配置管理、运行观测、模型接入、路由分析、profile、分类器训练、评估、上线与回滚。

## 核心概念

### 标准模型 Canonical Model

表示“能力上被视为同一个模型”的抽象模型，例如：

```yaml
id: gpt_5_5
display_name: GPT-5.5
family: gpt
vendor: openai
capabilities: [reasoning, code, long_context]
status: active
```

它不直接等于某个 API 地址。分类、评测、成本归因和上线策略都应该围绕标准模型展示。

### 接入源 Provider Endpoint

表示调用这个标准模型的一条具体通道，例如官方 API、第三方聚合 API、公司内部网关。

```yaml
id: gpt_5_5_openai
canonical_model: gpt_5_5
provider_model: openai/gpt-5.5
api_base_env: OPENAI_API_BASE
api_key_env: OPENAI_API_KEY
priority: 10
enabled: true
cost: 0.0
latency: null
```

同一个标准模型可以有多个 endpoint。运行时选择 endpoint，路由分类器仍然选择标准模型或 worker slot。

### Worker

当前 `configs/fugu.yaml` 里的 worker 本质是“可被 Fugu 路由选择的候选”。建议改成绑定标准模型，而不是直接绑定唯一 provider。

```yaml
workers:
  gpt_5_5:
    canonical_model: gpt_5_5
    endpoint_policy: cheapest_healthy
    enabled: true
    tags: [strong, reasoning]
    max_tokens: 1024
    temperature: 0.2
```

`endpoint_policy` 支持：

- `fixed`: 固定使用一个 endpoint。
- `cheapest_healthy`: 在健康 endpoint 中选成本最低。
- `fastest_healthy`: 在健康 endpoint 中选延迟最低。
- `priority`: 按优先级选择第一个健康 endpoint。
- `weighted`: 按权重分流，用于灰度或供应商压测。

### 分类器 Head

Flash head 是 `(n_workers, 1024)`，Pro head 是 `(HEAD_ROWS, 1024)`。它们和 worker 顺序强相关，所以后台必须记录：

- head 文件路径。
- 训练时使用的 worker 列表和顺序。
- 数据集路径。
- profile 文件路径。
- 训练命令、评估结果、创建时间。

如果新增标准模型并加入 worker 池，就需要重新 profile 并训练/评估 head。

## 控制台信息架构

### 1. 总览

展示：

- 服务状态：`/health`、端口、mock/live/local。
- 对外模型：`openfugu-flash`、`openfugu-pro`。
- 当前配置文件：默认 `configs/fugu.yaml`。
- 当前 flash/pro head：路径、worker 数、是否匹配当前 worker 池。
- 最近请求：模型、耗时、turn 数、失败数。

操作：

- 打开/关闭 debug。
- 重新加载配置。
- 下载当前配置快照。

### 2. 标准模型管理

展示：

- 标准模型列表：名称、family、能力标签、状态。
- 该模型下的 endpoint 数量。
- 当前默认 endpoint policy。
- 最近 profile 分数、平均延迟、错误率、成本。

操作：

- 新增标准模型。
- 编辑标签、状态、说明。
- 将模型加入 `openfugu-flash` 或 `openfugu-pro` 的候选池。
- 从候选池移除模型。

新增标准模型流程：

1. 填写 `id`、`display_name`、`family`、能力标签。
2. 至少添加一个 endpoint。
3. 跑 smoke test。
4. 选择数据集跑 profile。
5. 训练或刷新分类器 head。
6. 评估通过后启用。

### 3. Endpoint 管理

展示：

- endpoint 列表：所属标准模型、provider_model、api base env、enabled、优先级。
- 健康状态：最近成功/失败、平均延迟、错误信息。
- 成本元数据：输入/输出 token 单价，或人工填的成本权重。

操作：

- 新增官方 API 接入。
- 新增第三方 API 接入。
- 启用/禁用 endpoint。
- 调整 endpoint 优先级。
- 指定某个 worker 固定走某个 endpoint。
- 一键切换同一标准模型的当前 endpoint。

这里不保存明文密钥，只保存 env var 名称。密钥继续走环境变量。

### 4. Worker 池管理

展示：

- `openfugu-flash` worker 池。
- `openfugu-pro` worker 池。
- 每个 worker 的 enabled、tags、max_tokens、temperature、endpoint_policy。
- 当前 worker 顺序，因为 head 依赖顺序。

操作：

- 启用/禁用 worker。
- 调整参数。
- 调整 worker 顺序。
- 对比当前 worker 池与 head 训练时 worker 池。

危险操作提示：

- 改 worker 顺序会让旧 head 的行语义错位。
- 新增/删除 worker 后，旧 flash head 维度会不匹配。
- Pro head 可以取前 n 个 agent rows，但语义仍然依赖训练时的 worker 排列。

### 5. Profile 与分类器训练

展示：

- 数据集列表：`data/router_train*.jsonl`。
- profile 文件：`data/worker_profile*.jsonl`。
- 每个 worker 的平均分、错误率、平均延迟。
- oracle 分数、best single worker、router 分数。

操作：

- 对某个模型池运行 profile：

```bash
python train/profile_workers.py \
  --config configs/fugu.yaml \
  --dataset data/router_train.jsonl \
  --out data/worker_profile.jsonl \
  --model openfugu-flash
```

- 训练 flash head：

```bash
python train/train_flash_router.py \
  --model "$FUGU_MODEL" \
  --dataset data/router_train.jsonl \
  --profile data/worker_profile.jsonl \
  --out flash_head.npy
```

- 训练 pro head：

```bash
python train/train_pro_router.py \
  --dataset data/router_train.jsonl \
  --out pro_head.npy
```

后台负责创建训练任务、执行命令、记录 stdout/stderr、保存产物元数据，并把任务状态展示到训练任务列表。

### 6. Head 版本管理

展示：

- head 版本列表。
- 类型：flash/pro。
- 文件路径。
- 训练 worker 列表和顺序。
- 数据集、profile、训练参数。
- 评估结果。
- 当前是否被服务使用。

操作：

- 设为当前 flash head。
- 设为当前 pro head。
- 回滚到上一版。
- 标记废弃。

上线规则：

- head 维度必须匹配当前 worker 池。
- worker 顺序必须一致，或者后台明确执行过映射迁移。
- 评估分数不能低于 best single worker，除非人工强制上线。

### 7. 请求与路由观测

展示：

- 最近 N 条请求。
- 请求模型：flash/pro。
- selected worker。
- endpoint 实际选择。
- route reason。
- turn 数。
- 耗时。
- 错误。

Pro debug 展示：

- 每一 turn 的 agent_id。
- role。
- worker。
- reply 摘要。
- terminated_by。

请求日志持久化保存，支持按模型、worker、endpoint、状态、时间范围筛选。

### 8. 调用台

功能：

- 选择 `openfugu-flash` / `openfugu-pro`。
- 输入 messages。
- 开启 debug。
- 发送到 `/v1/chat/completions`。
- 展示 answer、usage、route trace、耗时。

用途：

- 新 endpoint smoke test。
- 新模型上线前人工检查。
- head 切换后的快速验证。

### 9. 配置校验

后台保存前必须校验：

- 每个 model 至少有一个 enabled worker。
- 每个 worker 引用的 canonical model 存在。
- 每个 enabled canonical model 至少有一个 enabled endpoint。
- endpoint 不保存明文 key。
- head 文件存在且 shape 正确。
- 当前 worker 池和 head 元数据一致。

## 配置结构

现有 legacy 配置：

```yaml
workers:
  gpt_5_5:
    provider_model: openai/gpt-5.5
```

控制台使用结构：

```yaml
models:
  openfugu-flash:
    mode: per_question
    workers: [gpt_5_5, gemini_3_5_flash, deepseek_v4_flash]

canonical_models:
  gpt_5_5:
    display_name: GPT-5.5
    family: gpt
    tags: [strong, reasoning]

endpoints:
  gpt_5_5_official:
    canonical_model: gpt_5_5
    provider_model: openai/gpt-5.5
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_API_BASE
    enabled: true
    priority: 10
  gpt_5_5_gateway:
    canonical_model: gpt_5_5
    provider_model: openai/gpt-5.5
    api_key_env: FUGU_API_KEY
    api_base_env: FUGU_BASE_URL
    enabled: true
    priority: 20

workers:
  gpt_5_5:
    canonical_model: gpt_5_5
    endpoint_policy: cheapest_healthy
    enabled: true
    max_tokens: 1024
    temperature: 0.2
```

Legacy 兼容：

- 如果 worker 仍然只有 `provider_model`，后台把它当成 legacy worker。
- 新增 `canonical_models` / `endpoints` 后，服务层优先按新结构解析。
- legacy 配置按兼容模式读取。

## 控制台边界

控制台负责：

- 总览。
- 标准模型管理。
- endpoint 管理。
- worker 池管理。
- profile 与分类器训练。
- head 版本管理。
- 请求与路由日志。
- 调用台。
- 配置校验、上线与回滚。

控制台不负责：

- 保存明文 API 密钥。
- 替代训练脚本里的算法实现。
- 修改数据集样本内容。
- 作为通用 CMS 或业务用户系统。
