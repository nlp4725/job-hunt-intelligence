"""LLM skill listing for a JD sample — the discovery half of the refresh.

The LLM never feeds production scoring. It only surfaces what the
deterministic taxonomy misses, with verbatim evidence, so a human-reviewed
change can add it. Evidence that isn't actually in the posting is dropped
here, before anything downstream sees it.

Resumable: re-running with the same --out skips jobs already extracted.

    ./venv/bin/python .claude/skills/taxonomy-refresh/llm_extract.py --sample runs/<date>/sample.json
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from _common import load_env, read_json, squash, write_json

load_env()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

PROMPT_VERSION = "2026-09-15"

# $ per 1M tokens, off-peak; peak is 2x. deepseek-v4-pro, api-docs.deepseek.com
# pricing page as checked 2026-09-14.
PRICES = {"deepseek-v4-pro": {"hit": 0.022, "miss": 0.66, "out": 1.98}}

PROMPT = """List every concrete technical skill, tool, framework, language, platform or method that this job
posting asks the CANDIDATE to have (required or preferred).

Exclude: soft skills (communication, ownership), years of experience, industries, and technologies that only
describe the company's own product rather than the candidate's work. Include degrees and certifications, but
mark them kind="credential".

For each item:
- name: short and canonical, the way a candidate would write it on a resume ("PyTorch", "Distributed systems")
- kind: tool | method | concept | practice | credential
- evidence: a fragment copied EXACTLY from the posting, under 12 words, that shows the requirement"""


class Skill(BaseModel):
    name: str = Field(description="Short canonical name, as written on a resume")
    kind: Literal["tool", "method", "concept", "practice", "credential"]
    evidence: str = Field(description="Fragment copied exactly from the posting, under 12 words")


class Skills(BaseModel):
    skills: list[Skill]


def extract_one(llm, job: dict, max_chars: int, attempts: int = 3):
    message = HumanMessage(f'Posting: "{job["title"]}"\n\n{job["raw_text"][:max_chars]}')
    for attempt in range(attempts):
        try:
            out = llm.invoke([SystemMessage(PROMPT), message])
            if out["parsed"] is None:
                raise ValueError(f"unparseable output: {out.get('parsing_error')}")
            usage = (out["raw"].response_metadata or {}).get("token_usage") or {}
            return out["parsed"].skills, usage
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--out", help="default: extraction.json next to the sample")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--limit", type=int, help="only the first N sampled jobs (smoke tests)")
    args = parser.parse_args()

    sample = read_json(args.sample)
    jobs = sample["jobs"][: args.limit] if args.limit else sample["jobs"]
    out_path = Path(args.out) if args.out else Path(args.sample).with_name("extraction.json")

    result = read_json(out_path) if out_path.exists() else {
        "sample_path": str(Path(args.sample).resolve()),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "jobs": [],
        "failed": [],
        "usage": {"hit": 0, "miss": 0, "out": 0},
        "evidence_dropped": 0,
    }
    done = {j["id"] for j in result["jobs"]}
    todo = [j for j in jobs if j["id"] not in done]
    print(f"{len(done)} already extracted, {len(todo)} to go")

    llm = ChatDeepSeek(model=args.model, extra_body={"thinking": {"type": "disabled"}}).with_structured_output(
        Skills, include_raw=True
    )

    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(extract_one, llm, job, args.max_chars): job for job in todo}
        for i, future in enumerate(as_completed(futures), 1):
            job = futures[future]
            try:
                skills, usage = future.result()
            except Exception as exc:
                result["failed"].append({"id": job["id"], "error": str(exc)[:200]})
                continue
            haystack = squash(job["raw_text"])
            kept = [s.model_dump() for s in skills if squash(s.evidence) and squash(s.evidence) in haystack]
            result["evidence_dropped"] += len(skills) - len(kept)
            result["jobs"].append({"id": job["id"], "track": job["track"], "title": job["title"], "skills": kept})
            hit = usage.get("prompt_cache_hit_tokens", 0)
            result["usage"]["hit"] += hit
            result["usage"]["miss"] += usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0) - hit)
            result["usage"]["out"] += usage.get("completion_tokens", 0)
            if i % 25 == 0:
                write_json(out_path, result)
                print(f"  {i}/{len(todo)}")

    price = PRICES.get(args.model)
    if price:
        u = result["usage"]
        off_peak = (u["hit"] * price["hit"] + u["miss"] * price["miss"] + u["out"] * price["out"]) / 1e6
        result["cost_usd"] = {"off_peak": round(off_peak, 4), "peak": round(2 * off_peak, 4)}
    write_json(out_path, result)

    n_skills = sum(len(j["skills"]) for j in result["jobs"])
    print(f"extracted {len(result['jobs'])} jobs, {n_skills} skills kept, "
          f"{result['evidence_dropped']} dropped for evidence not found in the posting, {len(result['failed'])} failed")
    if "cost_usd" in result:
        print(f"cost ≈ ${result['cost_usd']['off_peak']} off-peak / ${result['cost_usd']['peak']} peak")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
