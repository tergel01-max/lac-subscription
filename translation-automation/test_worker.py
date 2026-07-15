#!/usr/bin/env python3
"""
Offline test for lac_translate_worker.

No network / no API key / no ERPNext needed: OpenAI and the ERP client are
stubbed. Proves the parts that matter:
  - sentence segmentation and 1:1 source-sentence rows
  - count-enforced batching: if the model DROPS a sentence, it is detected
    and retried individually (nothing is silently lost)
  - suggestions + token/cost are written back per segment
  - export ordering / fallback (final > suggestion > draft)
"""
import json
import re
import sys
import types

# --- stub the `openai` module BEFORE importing the worker ---------------- #
class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content, usage):
        self.choices = [_Choice(content)]
        self.usage = usage


class _Completions:
    def __init__(self, parent):
        self.parent = parent

    def create(self, model, temperature, response_format, messages):
        self.parent.calls += 1
        user = messages[-1]["content"]
        ids = [int(x) for x in re.findall(r"\[(\d+)\] EN:", user)]
        # Simulate the model dropping id 5 the first time it appears in a
        # multi-item batch (the failure mode we must defend against).
        drop = None
        if len(ids) > 1 and 5 in ids and not self.parent.dropped_once:
            drop = 5
            self.parent.dropped_once = True
        items = [{"id": i, "mn": f"IMPROVED-{i}"} for i in ids if i != drop]
        return _Resp(json.dumps({"items": items}), _Usage(100, 40))


class _Chat:
    def __init__(self, parent):
        self.completions = _Completions(parent)


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.calls = 0
        self.dropped_once = False
        self.chat = _Chat(self)


_fake = types.ModuleType("openai")
_fake.OpenAI = FakeOpenAI
sys.modules["openai"] = _fake

import lac_translate_worker as w  # noqa: E402


# --- stub ERP ------------------------------------------------------------ #
class FakeERP:
    def __init__(self, segs, project):
        self.segs = {s["name"]: s for s in segs}
        self.project = project

    def get(self, dt, name):
        return dict(self.project)

    def get_list(self, dt, filters, fields, order_by="seq asc"):
        out = []
        for s in self.segs.values():
            ok = True
            for k, v in filters.items():
                if s.get(k) != v:
                    ok = False
            if ok:
                out.append({k: s.get(k) for k in fields})
        return sorted(out, key=lambda x: x.get("seq", 0))

    def update(self, dt, name, doc):
        if dt == "Translation Segment":
            self.segs[name].update(doc)
        else:
            self.project.update(doc)


class Args:
    def __init__(self, **k):
        self.__dict__.update(k)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    assert cond, name


# --- tests --------------------------------------------------------------- #
def test_segmentation():
    s = w.split_sentences("Hello world. How are you? I am fine! Good.")
    check("splits 4 sentences", len(s) == 4)
    check("keeps punctuation", s[1] == "How are you?")

    # unequal counts -> every source sentence still gets a draft hint
    aligned = w.align_draft(["a", "b", "c"], ["x", "y"])
    check("align covers all source sentences", len(aligned) == 3)

    # abbreviations must not split (regression from the MMOH book test)
    abbr = w.split_sentences("Based on Dr. Masquelier's work. It was groundbreaking.")
    check("does not split on 'Dr.'", len(abbr) == 2 and abbr[0].startswith("Based on Dr. Masquelier"))


def test_run_no_dropped_sentences():
    # 10 pending segments -> batches of 8 + 2; model drops one in batch 1
    segs = [
        {"name": f"S{i}", "seq": i, "status": "Pending", "project": "TRP-TEST",
         "source_text": f"Sentence number {i}.", "draft_text": f"draft {i}"}
        for i in range(1, 11)
    ]
    project = {"name": "TRP-TEST", "model": "gpt-4o-mini", "glossary": "keep tone"}
    erp = FakeERP(segs, project)

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    w.cmd_run(erp, Args(project="TRP-TEST", model=None))

    suggested = [s for s in erp.segs.values() if s["status"] == "Suggested"]
    check("all 10 segments got a suggestion", len(suggested) == 10)
    check("no segment left Pending",
          not [s for s in erp.segs.values() if s["status"] == "Pending"])
    check("no segment Rejected/dropped",
          not [s for s in erp.segs.values() if s["status"] == "Rejected"])
    check("every suggestion is populated",
          all(s.get("ai_suggestion") for s in erp.segs.values()))
    check("tokens recorded per segment",
          all(s.get("prompt_tokens", 0) > 0 for s in erp.segs.values()))
    check("project rolled up to Review", erp.project["status"] == "Review")


def test_export_fallback():
    segs = [
        {"name": "S1", "seq": 1, "project": "P", "final_text": "FINAL", "ai_suggestion": "SUG", "draft_text": "DRAFT"},
        {"name": "S2", "seq": 2, "project": "P", "final_text": "", "ai_suggestion": "SUG2", "draft_text": "DRAFT2"},
        {"name": "S3", "seq": 3, "project": "P", "final_text": "", "ai_suggestion": "", "draft_text": "DRAFT3"},
    ]
    erp = FakeERP(segs, {"name": "P"})
    out = "/tmp/claude-0/-home-user-lac-subscription/aed78016-470e-54b4-b439-881f40003ea8/scratchpad/_export.txt"
    w.cmd_export(erp, Args(project="P", out=out))
    with open(out, encoding="utf-8") as f:
        lines = f.read().split("\n")
    check("export prefers final, then suggestion, then draft",
          lines == ["FINAL", "SUG2", "DRAFT3"])


if __name__ == "__main__":
    test_segmentation()
    test_run_no_dropped_sentences()
    test_export_fallback()
    print("\nAll tests passed.")
