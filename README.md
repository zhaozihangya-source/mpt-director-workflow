# MPT Director Workflow

MoneyPrinterTurbo 的前置导演层、素材审核层、本地 WebUI 和成片 QA 工具。

这个项目不替换 MoneyPrinterTurbo。它把短视频生产拆成可复现的商业流程：

```text
DeepSeek 批量写稿
  -> Codex CLI 定稿
  -> Codex CLI 做镜头级导演判断
  -> director 决定 stock_video / codex_image
  -> Pexels API 优先采集并审核 stock video
  -> Codex 内置 image_gen 生成关键画面并登记
  -> asset_bindings 商业授权、适配和批准审核
  -> MoneyPrinterTurbo local materials 顺序渲染
  -> ffmpeg/ffprobe QA
  -> Codex CLI 语义审核
  -> revision_plan 决定 ready / revise
```

## Pipeline V2（推荐）

`tools/run_pipeline_v2.py` 是 4 阶段状态机编排器，相比逐步执行：

- **时长闭环**：定稿后用 edge-tts 实测旁白时长，不合格自动带反馈重写（最多 3 轮），不再依赖固定字速估算。
- **句级节奏**：每句实测时长回写 shot_plan 的 `duration_hint_seconds`，镜头时长不再拍脑袋。
- **per-shot 降级**：Pexels 搜不到素材的镜头自动降级为 Codex 生图，降级率超过 50% 才阻断。
- **渲染前结构预审**：零成本拦截镜头缺字段、搜索词重复、脚本为占位文本等结构问题。
- **断点续跑**：每阶段完成写入 `pipeline_state.json`，重跑自动跳过已完成阶段；Codex 生图登记完成后重跑即从断点继续。

```bash
python tools/scaffold_run.py "选题" --duration 60
python tools/run_pipeline_v2.py ../director-runs/<run_id> --show-logs
# 生图断点：用 Codex image_gen 生成 reports/codex_image_prompts/*.md 中的图片，
# 用 tools/register_codex_image.py 登记后重跑上面命令即续。
```

阶段：`script`（写稿+定稿+硬审+TTS 实测）→ `materials`（策略+采集+降级+预审+生图断点）→ `render`（MPT 渲染，轮询带网络容错）→ `acceptance`（技术 QA + Codex 语义审核 + 返修计划）。

## Open Source Scope

建议把 `director-workflow/` 作为 GitHub 仓库根目录发布，不要直接发布父目录。父目录通常会包含：

- `MoneyPrinterTurbo/` 上游源码或本地改动。
- `director-runs/` 生成稿件、素材、渲染视频和审核报告。
- 上传器 cookie、浏览器登录状态、平台草稿和本机绝对路径。

## Requirements

- Python 3.11+
- 本机 MoneyPrinterTurbo API 服务，默认 `http://127.0.0.1:8080/api/v1/videos`
- `ffmpeg` 和 `ffprobe`
- DeepSeek API key 或本机 `deepseek` CLI
- Codex CLI，用于定稿、导演策略和成片语义审核
- Codex Desktop / Codex 会话内置 `image_gen`，用于生成 `codex_image` 镜头
- Pexels API key，用于 stock video 采集

## Configuration

```bash
cd director-workflow
cp .env.example .env.local
```

只把真实密钥填到 `.env.local`、系统环境变量或 macOS Keychain，不要提交到 Git。

关键配置：

```bash
MPT_API_ENDPOINT=http://127.0.0.1:8080/api/v1/videos
DIRECTOR_RUNS_DIR=../director-runs
MPT_DIR=../MoneyPrinterTurbo
DIRECTOR_DEFAULT_VOICE_NAME=gemini:Zephyr-Female

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

CODEX_EXECUTABLE=codex
PEXELS_API_KEY=
# Only used when fallback is explicitly allowed.
PIXABAY_API_KEY=
```

DeepSeek 优先走 API。没有 API key 时，`--provider auto` 会回退到本机 `deepseek` CLI。

TTS 由 MoneyPrinterTurbo 执行。本项目只把 `DIRECTOR_DEFAULT_VOICE_NAME` 写入 MPT payload；对应 TTS provider 的密钥仍放在 MPT 自己的配置里。

Codex 不把 token 写入本项目。文字审核使用用户本机已经登录的 Codex CLI，或通过 `CODEX_EXECUTABLE` 指向兼容命令。Codex 内置生图不是 Python API，也不是 OpenAI API key 调用；WebUI 会生成生图 handoff，由当前 Codex 会话调用内置 `image_gen` 后再登记图片。

## Local WebUI

```bash
python tools/webui.py --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

WebUI 提供：

- 配置状态检查：MPT endpoint、DeepSeek、Codex、Pexels、上传目录。
- 本机配置入口：DeepSeek API、Pexels API、Codex CLI、MPT endpoint、TTS voice。
- 创建视频 run。
- 创建并严格生成视频：按 skill 串起写稿、Codex 定稿、Codex 导演策略、Pexels 采集、Codex 生图 handoff、素材绑定、MPT 渲染、QA 和语义审核。
- 自动运行当前 Run：对已选 run 执行同一条严格流水线。
- Codex 生图任务面板：显示 prompt、建议保存路径，并登记/批准生成图。
- 查看每个 run 的关键文件状态。
- 按白名单运行步骤，不允许任意 shell 命令。
- 查看后台任务 stdout/stderr，敏感字段会脱敏。

严格生成不会使用 Pixabay，除非显式允许 fallback。严格生成遇到 `codex_image` 镜头会停在 `waiting_for_imagegen`，写出 `image_generation_handoff.json`。用 Codex 内置 `image_gen` 生成图片并在 WebUI 登记后，再点击“严格运行当前 Run”，流程会从素材绑定继续，不会重写已批准的脚本和分镜。

## CLI Workflow

创建 run：

```bash
python tools/scaffold_run.py "美国每日新闻" --duration 60
```

生成 DeepSeek 候选稿：

```bash
python tools/generate_drafts.py ../director-runs/<run_id> --provider auto --count 5
```

自动定稿：

```bash
python tools/codex_approve_script.py ../director-runs/<run_id>
```

规则兜底生成素材策略：

```bash
python tools/plan_material_strategy.py ../director-runs/<run_id> --mode rules
```

Codex 生成素材策略：

```bash
python tools/plan_material_strategy.py ../director-runs/<run_id> --mode codex
```

Pexels 优先采集并绑定素材：

```bash
python tools/collect_api_materials.py ../director-runs/<run_id> --provider pexels --bind --approve-passing
```

导出 Codex 生图任务：

```bash
python tools/build_image_tasks.py ../director-runs/<run_id>
```

使用 Codex 内置 `image_gen` 按 `reports/codex_image_prompts/<shot_id>.md` 生成图片，保存到 `image_tasks.json` 里的 `suggested_output`。生成图片后登记到镜头：

```bash
python tools/register_codex_image.py ../director-runs/<run_id> s03 /path/to/generated.png --approve
```

刷新素材绑定，确认商业审核通过：

```bash
python tools/build_asset_bindings.py ../director-runs/<run_id>
```

构建 MPT payload：

```bash
python tools/build_mpt_params.py ../director-runs/<run_id>
```

提交渲染并等待：

```bash
python tools/submit_mpt.py ../director-runs/<run_id> --wait
```

成片 QA：

```bash
python tools/qa_render.py ../director-runs/<run_id> /path/to/final.mp4 --contact-sheet
```

Codex 语义审核：

```bash
python tools/run_semantic_review.py ../director-runs/<run_id>
```

生成返修计划：

```bash
python tools/build_revision_plan.py ../director-runs/<run_id>
```

## Key Files In A Run

- `brief.json`：选题、平台、时长、风格。
- `script_candidates.json`：DeepSeek 候选稿。
- `script_audit.json`：字数、句数、长句和泛词硬审。
- `approved_script.md`：最终旁白。
- `shot_plan.json`：逐句分镜和素材类型。
- `material_strategy.json`：Codex 或规则生成的素材策略。
- `api_asset_candidates.json`：Pexels/Pixabay 候选素材。
- `image_tasks.json`：Codex 生图任务。
- `asset_manifest.json`：素材清单和基础评分。
- `asset_bindings.json`：镜头到素材的最终绑定。
- `asset_binding_report.json`：素材授权、适配和批准状态，商业流程必须 `pass`。
- `mpt_params.json`：提交给 MPT 的参数。
- `qa_report.json`：技术 QA。
- `semantic_review.json`：Codex 发布前语义审核。
- `revision_plan.json`：最终是否 ready。

## Validation

```bash
python -m unittest discover -s tests
python -m compileall -q director_workflow tools
python tools/smoke_strict_flow.py
```

## Current Boundary

适合稳定生产 1-2 分钟竖屏短视频。更长视频需要增加章节级结构、素材去重、跨段节奏审核和更强的视觉 QA。

Codex 生图目前通过 `image_tasks.json` 和 `image_generation_handoff.json` 导出任务，再由当前 Codex 会话的内置 `image_gen` 生成图片，并用 WebUI 或 `register_codex_image.py` 登记。项目内不会直接保存 Codex 或 OpenAI API token。
