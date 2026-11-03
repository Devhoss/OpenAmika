"""Reflection output parsing (CONTRACT §1.3, C20/R13) on the archived corpus and on the
tolerance cases the contract lists. `parse_thinking_response` is AST-extracted from
payload/webui.py (tests/_po_support.extract_webui).

Corpus check (replay/corpus/reflection.jsonl, 184 unique outputs): whenever a guidance tag
has real content in the raw text (`<提示>…`, `<计划>…`, `<开口>…`, any bracket style), the
parser returns it; channels are the declared `通道:` list when there is one, else inferred
from the tags present; whenever `[分析]` is followed by text, the analysis is non-empty and
never contains a guidance tag.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _po_support import extract_webui, load_corpus, pending_on, prepare_prompt_namespace, short  # noqa: E402

TAGS = ("提示", "计划", "开口")
PLACEHOLDERS = {"", "（无）", "(无)", "无", "none", "n/a", "-", "—", "……", "省略"}
_BRACKETS = str.maketrans({"＜": "<", "〈": "<", "《": "<", "＞": ">", "〉": ">", "》": ">", "／": "/",
                           "［": "[", "］": "]", "【": "[", "】": "]"})


def raw_tag_content(raw, tag):
    text = (raw or "").translate(_BRACKETS)
    m = re.search(rf"<\s*{tag}\s*>(.*?)(?:<\s*/\s*{tag}\s*>|<\s*/?\s*(?:提示|计划|开口)\s*>|\[(?:分析|指导方式|指导内容)\]|\Z)",
                  text, re.S)
    return m.group(1).strip() if m else ""


@pending_on("brain")
class ReflectionParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = prepare_prompt_namespace(extract_webui())
        cls.parse = staticmethod(ns["parse_thinking_response"])
        cls.rows = load_corpus("reflection")

    def test_corpus_present(self):
        self.assertGreater(len(self.rows), 150)

    def test_corpus_guidance_and_analysis_recovered(self):
        bad = []
        for row in self.rows:
            raw = row["raw"]
            r = self.parse(raw)
            declared = re.search(r"通道\s*[:：]\s*(.+)", raw.translate(_BRACKETS))
            for tag in TAGS:
                want = raw_tag_content(raw, tag)
                if want.lower() not in PLACEHOLDERS:
                    if not r[tag].strip():
                        bad.append(f"{row['src']}:{row['id']}: <{tag}> content lost | {short(raw, 400)}")
                    elif not declared and tag not in r["channels"]:
                        # no 通道 line: channels are inferred from the tags present (CONTRACT §1.3)
                        bad.append(f"{row['src']}:{row['id']}: channel {tag} not inferred | {short(raw, 400)}")
            if declared:
                # a declared 通道 line stays authoritative (Build F behaviour)
                self.assertEqual(r["channels"], [t for t in TAGS if t in declared.group(1)], row["id"])
            tail = raw.translate(_BRACKETS)
            idx = tail.rfind("[分析]")
            if idx != -1 and tail[idx + 4:].strip():
                if not r["analysis"].strip():
                    bad.append(f"{row['src']}:{row['id']}: [分析] lost | {short(raw, 400)}")
                elif re.search(r"<\s*/?\s*(?:提示|计划|开口)\s*>", r["analysis"]):
                    bad.append(f"{row['src']}:{row['id']}: guidance tag inside the analysis | {short(raw, 400)}")
            self.assertTrue(set(r["channels"]) <= set(TAGS), r["channels"])
        self.assertEqual(bad, [], f"{len(bad)} problems in {len(self.rows)} outputs:\n" + "\n".join(bad[:5]))

    def test_tolerance_cases(self):
        # missing 通道 line: inferred from the tags present
        r = self.parse("[指导方式]\n\n[指导内容]\n<计划>先问清楚他住哪。</计划>\n\n[分析]\n他说法前后矛盾。")
        self.assertEqual(r["channels"], ["计划"])
        self.assertEqual(r["计划"], "先问清楚他住哪。")
        self.assertEqual(r["analysis"], "他说法前后矛盾。")
        # unclosed tag: taken up to the next tag / section or the end
        r = self.parse("[指导方式] 通道: 提示\n[指导内容]\n<提示>轻松一点，逗他一句。\n\n[分析]\n气氛不错。")
        self.assertEqual(r["提示"], "轻松一点，逗他一句。")
        self.assertEqual(r["analysis"], "气氛不错。")
        r = self.parse("[指导方式] 通道: 开口\n<开口>问问他周末去不去河边")
        self.assertEqual(r["开口"], "问问他周末去不去河边")
        self.assertIn("开口", r["channels"])
        # [分析] before the guidance: the last occurrence wins, guidance still parsed
        r = self.parse("先说明：我会先写[分析]。\n[分析]\n早先的笔记。\n[指导方式] 通道: 提示\n<提示>别较真。</提示>\n[分析]\n最终分析。")
        self.assertEqual(r["analysis"], "最终分析。")
        self.assertEqual(r["提示"], "别较真。")
        # fullwidth brackets
        r = self.parse("［指导方式］ 通道：计划\n＜计划＞下次提起咖啡店。＜／计划＞\n【分析】\n计划持续。")
        self.assertEqual(r["计划"], "下次提起咖啡店。")
        self.assertEqual(r["channels"], ["计划"])
        self.assertEqual(r["analysis"], "计划持续。")


if __name__ == "__main__":
    unittest.main()
