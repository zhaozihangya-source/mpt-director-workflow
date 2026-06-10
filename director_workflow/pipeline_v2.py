"""Pipeline V2: 4 阶段状态机编排器。

与 runtime.run_auto_pipeline 的区别：
1. script 阶段带 TTS 时长自动返修循环（不合格自动带反馈重写，不是阻断）。
2. materials 阶段 per-shot 降级：搜不到素材的 stock_video 镜头自动转 codex_image。
3. 句级实测时长回写 shot_plan 的 duration_hint_seconds。
4. 渲染前结构预审（pre_render_check），免费拦截结构性问题。
5. 每阶段完成即写入 pipeline_state.json checkpoint，重入自动跳过。
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, load_runtime_config
from .io_utils import count_cjk_chars, read_json, write_json
from .mpt_tools import resolve_mpt_output_video, validate_shot_plan
from .runtime import build_imagegen_handoff, child_env
from .material_strategy import build_codex_image_prompt

STAGES = ("script", "materials", "render", "acceptance")
STATE_FILENAME = "pipeline_state.json"

MAX_SCRIPT_ATTEMPTS = 3
# 降级镜头占比超过该阈值说明素材体系出了问题，不再继续
MAX_DEGRADE_RATIO = 0.5


@dataclass
class StageResult:
    stage: str
    status: str  # complete / failed / waiting_for_imagegen
    detail: str = ""
    logs: list[str] = field(default_factory=list)


class PipelineV2:
    def __init__(
        self,
        run_dir: Path,
        config: RuntimeConfig | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.config = config or load_runtime_config()
        self.options = options or {}
        self.logs: list[str] = []

    # ---------- state ----------

    @property
    def state_path(self) -> Path:
        return self.run_dir / STATE_FILENAME

    def load_state(self) -> dict[str, Any]:
        state = read_json(self.state_path, None)
        if not isinstance(state, dict) or state.get("version") != 2:
            state = {"version": 2, "stages": {}}
        state.setdefault("stages", {})
        return state

    def save_stage(self, state: dict[str, Any], result: StageResult, **extra: Any) -> None:
        entry = {"status": result.status, "detail": result.detail, **extra}
        state["stages"][result.stage] = entry
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(self.state_path, state)

    # ---------- tool runner ----------

    def _run_tool(self, script_name: str, *args: str, timeout: int = 900) -> tuple[int, str, str]:
        command = [
            sys.executable,
            str(self.config.workflow_dir / "tools" / script_name),
            str(self.run_dir),
            *args,
        ]
        self.logs.append("$ " + " ".join(command))
        try:
            result = subprocess.run(
                command,
                cwd=str(self.config.root_dir),
                env=child_env(self.config),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.logs.append(f"timeout after {timeout}s")
            return 124, "", f"timeout after {timeout}s"
        if result.stdout.strip():
            self.logs.append(result.stdout.strip())
        if result.stderr.strip():
            self.logs.append(result.stderr.strip())
        return result.returncode, result.stdout, result.stderr

    # ---------- stage 1: script ----------

    def stage_script(self) -> StageResult:
        provider = str(self.options.get("provider") or "auto")
        count = str(self.options.get("count") or 5)
        codex_model = str(self.options.get("codex_model") or "")

        for attempt in range(1, MAX_SCRIPT_ATTEMPTS + 1):
            self.logs.append(f"--- script attempt {attempt}/{MAX_SCRIPT_ATTEMPTS} ---")
            code, _, _ = self._run_tool(
                "generate_drafts.py", "--provider", provider, "--count", count, timeout=600
            )
            if code != 0:
                return StageResult("script", "failed", f"generate_drafts exit {code}")

            approve_args = ["--timeout", "750"]
            if codex_model:
                approve_args += ["--model", codex_model]
            code, _, _ = self._run_tool("codex_approve_script.py", *approve_args, timeout=900)
            if code != 0:
                return StageResult("script", "failed", f"codex_approve_script exit {code}")

            # 硬审仅记录，不阻断（定稿人是 Codex，硬审结果供复盘）
            self._run_tool("audit_script.py", timeout=120)

            code, _, _ = self._run_tool("tts_dry_run.py", timeout=120)
            tts_report = read_json(self.run_dir / "tts_dry_run_report.json", {})
            decision = str(tts_report.get("decision") or "")
            if code == 0:
                # pass 或 skipped（edge-tts 不可用），都放行
                self._clear_revision_feedback()
                return StageResult(
                    "script",
                    "complete",
                    f"attempt={attempt} tts={decision or 'unknown'}",
                )
            # 时长不合格：把建议写进 brief，下一轮 DeepSeek prompt 会带上
            recommendation = str(tts_report.get("recommendation") or "时长不合格，请调整字数")
            self.logs.append(f"tts feedback: {recommendation}")
            self._set_revision_feedback(recommendation)

        return StageResult(
            "script", "failed", f"时长在 {MAX_SCRIPT_ATTEMPTS} 轮内未收敛"
        )

    def _set_revision_feedback(self, feedback: str) -> None:
        brief = read_json(self.run_dir / "brief.json", {})
        brief["revision_feedback"] = feedback
        write_json(self.run_dir / "brief.json", brief)

    def _clear_revision_feedback(self) -> None:
        brief = read_json(self.run_dir / "brief.json", {})
        if "revision_feedback" in brief:
            brief.pop("revision_feedback")
            write_json(self.run_dir / "brief.json", brief)

    # ---------- stage 2: materials ----------

    def stage_materials(self) -> StageResult:
        codex_model = str(self.options.get("codex_model") or "")
        materials_mode = str(self.options.get("materials_mode") or "codex")

        if materials_mode == "stock_only":
            # 全镜头 Pexels 真实视频：无生图断点，适合无 Codex 生图能力的全自动场景
            code, _, _ = self._run_tool(
                "plan_material_strategy.py", "--mode", "stock-only", timeout=300
            )
            if code != 0:
                return StageResult("materials", "failed", "stock-only 素材策略生成失败")
        else:
            strategy_args = ["--mode", "codex", "--timeout", "750"]
            if codex_model:
                strategy_args += ["--model", codex_model]
            code, _, _ = self._run_tool("plan_material_strategy.py", *strategy_args, timeout=900)
            if code != 0:
                self.logs.append("codex strategy failed; falling back to rules mode")
                code, _, _ = self._run_tool(
                    "plan_material_strategy.py", "--mode", "rules", timeout=300
                )
                if code != 0:
                    return StageResult("materials", "failed", "素材策略生成失败（codex 与 rules 均失败）")

        # Pexels 采集允许部分失败，缺口由降级处理。
        # 候选池放大到每镜头 6 个，跨镜头素材去重才有得选。
        self._run_tool(
            "collect_api_materials.py",
            "--provider", "pexels", "--bind", "--approve-passing",
            "--max-candidates-per-shot", "6",
            timeout=1800,
        )

        if materials_mode == "stock_only":
            # 没有生图能力，降级无意义；素材缺口留给 binding report 直接暴露
            degraded = []
        else:
            degraded = self.degrade_uncovered_shots()
            shot_plan = read_json(self.run_dir / "shot_plan.json", {})
            total_shots = len(shot_plan.get("shots", []) or [])
            if total_shots and len(degraded) / total_shots > MAX_DEGRADE_RATIO:
                return StageResult(
                    "materials",
                    "failed",
                    f"降级镜头过多（{len(degraded)}/{total_shots}），素材采集基本失效",
                )
            if degraded:
                self.logs.append(f"degraded to codex_image: {','.join(degraded)}")

        self.apply_sentence_durations()

        check = self.pre_render_check()
        if not check["valid"]:
            return StageResult(
                "materials",
                "failed",
                "结构预审不通过: " + "; ".join(check["issues"][:5]),
            )

        code, _, _ = self._run_tool("build_image_tasks.py", timeout=300)
        if code != 0:
            return StageResult("materials", "failed", f"build_image_tasks exit {code}")

        handoff = build_imagegen_handoff(self.run_dir)
        if handoff["pending_count"]:
            return StageResult(
                "materials",
                "waiting_for_imagegen",
                f"等待 Codex 生图 {handoff['pending_count']} 张",
            )

        return self.finish_materials()

    def finish_materials(self) -> StageResult:
        """imagegen 完成后（或没有生图任务时）收尾素材阶段。"""
        code, _, _ = self._run_tool("build_asset_bindings.py", timeout=300)
        if code != 0:
            return StageResult("materials", "failed", f"build_asset_bindings exit {code}")
        report = read_json(self.run_dir / "asset_binding_report.json", {})
        if report.get("decision") != "pass":
            return StageResult(
                "materials", "failed", f"素材绑定审核未通过: {report.get('decision')}"
            )
        return StageResult("materials", "complete", "素材齐备且审核通过")

    def degrade_uncovered_shots(self) -> list[str]:
        """stock_video 镜头若没有任何已批准绑定素材，降级为 codex_image。"""
        shot_plan = read_json(self.run_dir / "shot_plan.json", {})
        bindings = read_json(self.run_dir / "asset_bindings.json", {})
        brief = read_json(self.run_dir / "brief.json", {})
        topic = str(brief.get("topic") or "")

        covered = {
            str(b.get("shot_id"))
            for b in bindings.get("bindings", []) or []
            if isinstance(b, dict) and b.get("approved") and str(b.get("asset_path") or "").strip()
        }
        degraded: list[str] = []
        shots = shot_plan.get("shots", []) or []
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            if str(shot.get("material_type")) != "stock_video":
                continue
            shot_id = str(shot.get("id") or "")
            if shot_id in covered:
                continue
            shot["material_type"] = "codex_image"
            shot["decision_reason"] = "Pexels 无合格素材，自动降级为 Codex 生图。"
            if not str(shot.get("codex_image_prompt") or "").strip():
                shot["codex_image_prompt"] = build_codex_image_prompt(
                    str(shot.get("narration") or ""), topic
                )
            degraded.append(shot_id)
        if degraded:
            shot_plan["validation"] = validate_shot_plan(shot_plan)
            write_json(self.run_dir / "shot_plan.json", shot_plan)
        return degraded

    def apply_sentence_durations(self) -> int:
        """把 tts_dry_run 的句级实测时长回写 shot_plan（按 index 对齐，1 句 = 1 镜头）。"""
        tts_report = read_json(self.run_dir / "tts_dry_run_report.json", {})
        durations = tts_report.get("sentence_durations") or []
        shot_plan = read_json(self.run_dir / "shot_plan.json", {})
        shots = shot_plan.get("shots", []) or []
        if not durations or not shots:
            return 0
        applied = 0
        for shot, item in zip(shots, durations):
            if not isinstance(shot, dict) or not isinstance(item, dict):
                continue
            seconds = float(item.get("estimated_seconds") or 0)
            if seconds > 0:
                # MPT 的 clip duration 是整数秒；向上取整保证素材不短于旁白
                shot["duration_hint_seconds"] = max(2, round(seconds + 0.49))
                applied += 1
        if applied:
            write_json(self.run_dir / "shot_plan.json", shot_plan)
        if len(durations) != len(shots):
            self.logs.append(
                f"warning: sentence/shot count mismatch ({len(durations)} vs {len(shots)})"
            )
        return applied

    def pre_render_check(self) -> dict[str, Any]:
        """渲染前结构预审：确定性规则，零成本拦截结构问题。"""
        issues: list[str] = []
        shot_plan = read_json(self.run_dir / "shot_plan.json", {})
        validation = validate_shot_plan(shot_plan)
        for item in validation.get("issues", []):
            issues.append(f"{item.get('shot_id')}: {item.get('issue')}")

        # 60 字以下不可能是正式脚本（scaffold 占位文本约 25 字也会被拦住）
        script_path = self.run_dir / "approved_script.md"
        script_text = (
            script_path.read_text(encoding="utf-8").replace("# Approved Script", "").strip()
            if script_path.exists()
            else ""
        )
        if count_cjk_chars(script_text) < 60:
            issues.append("approved_script.md 缺失、为空或仍是占位文本")

        # 搜索词重复只是风险信号（同词仍可选出不同素材），降为 warning；
        # 绑定层素材跨镜头重复才是成片画面重复的硬伤，阻断。
        warnings: list[str] = []
        shots = shot_plan.get("shots", []) or []
        seen_terms: set[str] = set()
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            sid = str(shot.get("id") or "?")
            if str(shot.get("material_type")) == "stock_video":
                terms = tuple(str(t).strip().lower() for t in shot.get("search_terms") or [])
                key = "|".join(terms)
                if key and key in seen_terms:
                    warnings.append(f"{sid}: search_terms 与其他镜头完全重复")
                seen_terms.add(key)

        bindings = read_json(self.run_dir / "asset_bindings.json", {})
        seen_assets: dict[str, str] = {}
        for binding in bindings.get("bindings", []) or []:
            if not isinstance(binding, dict):
                continue
            sid = str(binding.get("shot_id") or "?")
            asset = str(binding.get("provider_asset_id") or binding.get("asset_path") or "").strip()
            if not asset:
                continue
            if asset in seen_assets:
                issues.append(f"{sid}: 与 {seen_assets[asset]} 绑定了同一素材，成片画面会重复")
            else:
                seen_assets[asset] = sid

        report = {"valid": not issues, "issues": issues, "warnings": warnings}
        write_json(self.run_dir / "pre_render_check.json", report)
        return report

    # ---------- stage 3: render ----------

    def stage_render(self) -> StageResult:
        code, _, _ = self._run_tool("build_mpt_params.py", timeout=300)
        if code != 0:
            return StageResult("render", "failed", f"build_mpt_params exit {code}")
        code, _, _ = self._run_tool(
            "submit_mpt.py",
            "--wait",
            "--timeout-seconds", str(int(self.options.get("timeout_seconds") or 1200)),
            "--poll-seconds", "10",
            "--stall-seconds", "300",
            timeout=int(self.options.get("process_timeout") or 1800),
        )
        if code != 0:
            return StageResult("render", "failed", f"submit_mpt exit {code}")
        video = resolve_mpt_output_video(self.run_dir, mpt_dir=self.config.mpt_dir)
        if not video:
            return StageResult("render", "failed", "渲染完成但找不到输出视频")
        return StageResult("render", "complete", str(video))

    # ---------- stage 4: acceptance ----------

    def stage_acceptance(self) -> StageResult:
        video = resolve_mpt_output_video(self.run_dir, mpt_dir=self.config.mpt_dir)
        if not video:
            return StageResult("acceptance", "failed", "找不到渲染输出视频")
        code, _, _ = self._run_tool(
            "qa_render.py", str(video), "--contact-sheet", timeout=300
        )
        if code != 0:
            return StageResult("acceptance", "failed", f"qa_render exit {code}")

        # 语义审核失败不阻断验收（Codex 不可用时仍能给出技术 QA 结论）
        codex_model = str(self.options.get("codex_model") or "")
        semantic_args = ["--model", codex_model] if codex_model else []
        self._run_tool("run_semantic_review.py", *semantic_args, timeout=900)

        self._run_tool("build_revision_plan.py", timeout=120)

        qa = read_json(self.run_dir / "qa_report.json", {})
        semantic = read_json(self.run_dir / "semantic_review.json", {})
        qa_ok = qa.get("decision") == "pass"
        semantic_decision = str(semantic.get("decision") or "")
        semantic_ok = semantic_decision in {"pass", "", "not_reviewed"}
        if qa_ok and semantic_ok:
            return StageResult("acceptance", "complete", "ready")
        return StageResult(
            "acceptance",
            "failed",
            f"needs_revision: qa={qa.get('decision')} semantic={semantic_decision or 'skipped'}",
        )

    # ---------- main entry ----------

    def run(self) -> dict[str, Any]:
        state = self.load_state()
        results: list[StageResult] = []

        for stage in STAGES:
            entry = state["stages"].get(stage, {})
            status = entry.get("status", "")
            if status == "complete":
                self.logs.append(f"skip {stage}: already complete")
                continue

            if stage == "script":
                result = self.stage_script()
            elif stage == "materials":
                if status == "waiting_for_imagegen":
                    # 重入：检查生图是否都登记完
                    handoff = build_imagegen_handoff(self.run_dir)
                    if handoff["pending_count"]:
                        result = StageResult(
                            "materials",
                            "waiting_for_imagegen",
                            f"仍有 {handoff['pending_count']} 张生图未登记",
                        )
                    else:
                        result = self.finish_materials()
                else:
                    result = self.stage_materials()
            elif stage == "render":
                result = self.stage_render()
            else:
                result = self.stage_acceptance()

            self.save_stage(state, result)
            results.append(result)
            if result.status != "complete":
                break

        final = results[-1] if results else StageResult("none", "complete", "全部阶段已完成")
        return {
            "status": final.status,
            "stage": final.stage,
            "detail": final.detail,
            "stages": state["stages"],
            "logs": self.logs,
        }


def run_pipeline_v2(
    run_dir: Path,
    config: RuntimeConfig | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return PipelineV2(run_dir, config=config, options=options).run()
