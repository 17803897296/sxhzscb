"""从交接包源码里静态提取问卷常量，生成 questionnaire.json（供离线页面使用）。"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_handoff/shuangxuan/app/careersafari")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("demo/questionnaire.json")

WANT_PROFILE = ["PERSONA_OPTIONS", "REGION_OPTIONS", "DEALBREAKER_OPTIONS", "RIASEC_QUESTIONS",
                "VALUE_DIMENSIONS", "MBTI_CAVEAT", "TRANSFERABLE_SKILLS", "MAJOR_DIRECTIONS",
                "COMMON_DEGREE_COURSES", "PROGRAM_MILESTONES"]
WANT_FAIR = ["INDUSTRIES", "COMPANY_TYPES", "ON_SITE_GOALS", "AVAILABILITY", "RESUME_STATUS",
             "STAGE_DETAILS", "TARGET_STAGES", "TIMELINE_OPTIONS", "DEGREES",
             "MAX_RESUME_BYTES", "MAX_RESUME_CHARS", "FAIR_DOC_VERSION"]


def harvest(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    env = {}
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            target, value = node.target.id, node.value
        if target is None:
            continue
        try:
            env[target] = ast.literal_eval(value)
        except Exception:
            if isinstance(value, ast.Name) and value.id in env:
                env[target] = env[value.id]
    missing = [n for n in names if n not in env]
    return {n: env[n] for n in names if n in env}, missing


prof, miss1 = harvest(ROOT / "profile.py", WANT_PROFILE)
fair, miss2 = harvest(ROOT / "fair.py", WANT_FAIR)
schema_env, _ = harvest(ROOT / "schema.py", ["SCHEMA_VERSION", "MAJOR_DIRECTIONS"])
prof.update({k: v for k, v in schema_env.items() if k == "MAJOR_DIRECTIONS"})
fair["MAX_RESUME_BYTES"] = 20 * 1024 * 1024

doc = {
    "ok": True,
    "schema_version": schema_env.get("SCHEMA_VERSION", "0.3.0"),
    "persona_options": prof.get("PERSONA_OPTIONS", []),
    "regions": prof.get("REGION_OPTIONS", []),
    "dealbreakers": prof.get("DEALBREAKER_OPTIONS", []),
    "riasec_questions": prof.get("RIASEC_QUESTIONS", []),
    "value_dimensions": prof.get("VALUE_DIMENSIONS", []),
    "mbti_caveat": prof.get("MBTI_CAVEAT", ""),
    "transferable_skills": prof.get("TRANSFERABLE_SKILLS", []),
    "job_specific_skills": {},
    "major_directions": prof.get("MAJOR_DIRECTIONS", []),
    "common_degree_courses": prof.get("COMMON_DEGREE_COURSES", []),
    "program_milestones": prof.get("PROGRAM_MILESTONES", []),
    "fair": {
        "doc_version": fair.get("FAIR_DOC_VERSION", "1.1.0"),
        "target_stages": fair.get("TARGET_STAGES", []),
        "stage_details": fair.get("STAGE_DETAILS", []),
        "industries": fair.get("INDUSTRIES", []),
        "company_types": fair.get("COMPANY_TYPES", []),
        "on_site_goals": fair.get("ON_SITE_GOALS", []),
        "availability": fair.get("AVAILABILITY", []),
        "resume_status": fair.get("RESUME_STATUS", []),
        "timeline_options": fair.get("TIMELINE_OPTIONS", []),
        "degrees": fair.get("DEGREES", []),
        "max_resume_bytes": fair.get("MAX_RESUME_BYTES", 8388608),
        "max_resume_chars": fair.get("MAX_RESUME_CHARS", 20000),
        "llm_configured": False,
    },
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("生成", OUT)
print("  major_directions:", len(doc["major_directions"]))
print("  regions:", len(doc["regions"]))
print("  value_dimensions:", len(doc["value_dimensions"]))
print("  company_types:", len(doc["fair"]["company_types"]))
if miss1: print("  未取到(profile):", miss1)
if miss2: print("  未取到(fair):", miss2)
