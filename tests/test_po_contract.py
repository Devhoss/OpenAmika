"""Contract module tests (shared by brain and speech layer)."""
import sys
import unittest
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parents[1] / "payload"
sys.path.insert(0, str(PAYLOAD))
import po_contract as pc  # noqa: E402


class EmotionTests(unittest.TestCase):
    def test_69_real_keys(self):
        self.assertEqual(len(pc.VALID_EMOTIONS), 69)
        self.assertIn("whisper_sleepy", pc.VALID_EMOTIONS)

    def test_resolution(self):
        cases = {
            "casual_happy": "casual_happy", "[casual_happy]": "casual_happy",
            "casual-happy": "casual_happy", "Casual Happy": "casual_happy",
            "casual_shy 0.4": "casual_shy", "casual_very_shy": "casual_shy",
            "whisper_playful": "whisper_smile", "serious_caring": "serious_protective",
            "casual_curious": "casual_smile", "happy": "casual_happy", "excited": "casual_very_happy",
            "totally_made_up": "casual_neutral", "": "casual_neutral", None: "casual_neutral",
            "casual_embarassed": "casual_embarrassed", "intimate_angry": "intimate_mumbling",
            "casual_shy,firm": "casual_shy", "formal_happy": "polite_happy", "whisper": "whisper_neutral",
        }
        for tag, want in cases.items():
            self.assertEqual(pc.resolve_emotion(tag), want, tag)

    def test_every_register_suffix_combination_stays_in_register(self):
        suffixes = {e for es in pc.EMOTION_SETS.values() for e in es}
        for r in pc.REGISTERS:
            for e in suffixes:
                res = pc.resolve_emotion(f"{r}_{e}")
                self.assertIn(res, pc.VALID_EMOTIONS)
                self.assertTrue(res.startswith(r + "_"), (r, e, res))
        for r, m in pc.REGISTER_FALLBACK.items():
            for src, dst in m.items():
                self.assertIn(dst, pc.EMOTION_SETS[r], (r, src, dst))

    def test_vocab_lines_roundtrip(self):
        text = "\n".join(pc.emotion_vocab_lines())
        found = {m.group(1): set(m.group(2).split()) for m in pc._VOCAB_LINE_RE.finditer(text)}
        for r in pc.REGISTERS:
            self.assertEqual(found[r], set(pc.EMOTION_SETS[r]))


class SfxTests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(pc.canonical_sfx("laugh"), "Laugh")
        self.assertEqual(pc.canonical_sfx("Can_t_help_laughing"), "Can_t_help_laughing")
        self.assertEqual(pc.canonical_sfx("throat clear"), "Throat_clear")
        self.assertIsNone(pc.canonical_sfx("声效名"))
        self.assertIsNone(pc.canonical_sfx("register_emotion"))
        self.assertIsNone(pc.canonical_sfx("explosion"))

    def test_strip(self):
        self.assertEqual(pc.strip_sfx("哈哈【Laugh】不行了【声效名】").strip(), "哈哈 不行了")
        self.assertEqual(pc.strip_sfx("想一下【想想】"), "想一下【想想】")
        self.assertEqual(pc.strip_controls("想一下【想想】"), "想一下")


class DeepExtractTests(unittest.TestCase):
    def test_tagged_segments_and_thought(self):
        raw = ('[casual_happy]"哈哈，太好笑了。【Laugh】不行我缓缓。"[casual_shy]"才、才没有。"\n'
               '我心想：他又在逗我。我转了转杯子。【想想】')
        r = pc.extract_deep(raw)
        self.assertEqual([s["tone"] for s in r["segments"]], ["casual_happy", "casual_shy"])
        self.assertEqual(r["segments"][0]["text"], "哈哈，太好笑了。【Laugh】不行我缓缓。")
        self.assertEqual(r["speech"], "哈哈，太好笑了。 不行我缓缓。 才、才没有。")
        self.assertEqual(r["thought"], "他又在逗我。我转了转杯子。")
        self.assertTrue(r["needs_help"])
        self.assertFalse(r["wants_continue"])

    def test_untagged_quote_before_think_is_speech_after_think_is_thought(self):
        raw = ('我说："Sure, I can hear you."\n'
               '我心想："Unlike some loner I know." 他又提到别人了。')
        r = pc.extract_deep(raw)
        self.assertEqual(len(r["segments"]), 1)
        self.assertEqual(r["segments"][0]["text"], "Sure, I can hear you.")
        self.assertEqual(r["segments"][0]["tone"], "casual_neutral")
        self.assertIn("Unlike some loner I know", r["thought"])

    def test_untagged_quote_inherits_last_tag(self):
        raw = '[intimate_shy]"嗯。"\n"其实我也想去。"\n我心想：说出来了。'
        r = pc.extract_deep(raw)
        self.assertEqual([s["tone"] for s in r["segments"]], ["intimate_shy", "intimate_shy"])

    def test_loose_tag_without_quotes(self):
        raw = "[casual happy] Hi there, long time no see.\n我心想：他来了。"
        r = pc.extract_deep(raw)
        self.assertEqual(r["segments"], [{"text": "Hi there, long time no see.", "tone": "casual_happy"}])

    def test_sanitiser(self):
        raw = '[casual_playful]"*笑了一下* Sure.\\nWhy not? [casual_shy]"'
        r = pc.extract_deep(raw)
        self.assertEqual(r["segments"][0]["text"], "Sure. Why not?")

    def test_emphasis_kept_action_dropped(self):
        self.assertEqual(pc.sanitize_speech("Paper Moon. *The Glass Orchard*. Fine."), "Paper Moon. The Glass Orchard. Fine.")
        self.assertEqual(pc.sanitize_speech("*叹了口气* 好吧。"), "好吧。")

    def test_blurt_dedupe(self):
        raw = '[casual_neutral]"Booking every minute, huh… That sounds exhausting."\n我心想：嗯。'
        r = pc.extract_deep(raw, blurt="Booking every minute, huh…")
        self.assertEqual(r["segments"][0]["text"], "That sounds exhausting.")
        r2 = pc.extract_deep('[casual_neutral]"Booking every minute, huh…"\n我心想：嗯。', blurt="Booking every minute, huh…")
        self.assertEqual(r2["segments"], [])
        self.assertEqual(pc.dedupe_blurt("Hello there.", "hi"), "Hello there.")

    def test_thought_first_ordering_and_japanese_quotes(self):
        raw = '我心想：先想清楚。\n---\n[casual_neutral]「うん、聞こえてる。」'
        r = pc.extract_deep(raw)
        self.assertEqual(r["segments"][0]["text"], "うん、聞こえてる。")
        self.assertEqual(r["thought"], "先想清楚。")

    def test_corner_brackets_inside_ascii_quote_stay_inside(self):
        raw = '[casual_smile]"まず『わからない部分がある』って言うこと。『あなたは間違ってる』じゃなくて。"\n我心想：好。'
        r = pc.extract_deep(raw)
        self.assertEqual(len(r["segments"]), 1)
        self.assertIn("あなたは間違ってるじゃなくて", r["segments"][0]["text"])

    def test_nested_tag_closes_previous_segment(self):
        raw = '[casual_curious]"Space exhibit and old maps? Interesting mix.\\n[casual_playful]"You two really are cousins, huh?"\n我心想：嗯。'
        r = pc.extract_deep(raw)
        self.assertEqual([s["text"] for s in r["segments"]], ["Space exhibit and old maps? Interesting mix.", "You two really are cousins, huh?"])

    def test_quotation_inside_narration_is_not_speech(self):
        raw = '[casual_tired]"...Sometimes I just want to stop."\n【想想】\n他说这句话的时候，眼神有点散。\n"每个人都在期待你把事情做好"——这句话对我而言太熟悉了。'
        r = pc.extract_deep(raw)
        self.assertEqual(len(r["segments"]), 1)
        self.assertIn("每个人都在期待", r["thought"])

    def test_parenthetical_after_tag_is_thought(self):
        raw = '[casual_playful]"I mean… it\'s a pothos."[casual_thinking]（既然他认真想过，我可以给点建议。）'
        r = pc.extract_deep(raw)
        self.assertEqual(len(r["segments"]), 1)

    def test_multiline_tagged_quote(self):
        raw = '[casual_neutral]"So... same museum.\nAnyway, where exactly?"\n我心想：好。'
        r = pc.extract_deep(raw)
        self.assertEqual(r["segments"][0]["text"], "So... same museum. Anyway, where exactly?")

    def test_empty(self):
        r = pc.extract_deep("")
        self.assertEqual(r, {"segments": [], "speech": "", "thought": "", "needs_help": False, "wants_continue": False})


class SurfaceTests(unittest.TestCase):
    def test_full(self):
        raw = "status: ok\ntone: casual_neutral\nmode: say+think\nreply: My plan, huh…\nrecall: 计划"
        s = pc.parse_surface(raw)
        self.assertEqual(s["mode"], "say+think")
        self.assertEqual(s["reply"], "My plan, huh…")
        self.assertEqual(s["tone"], "casual_neutral")
        self.assertEqual(s["status"], "ok")
        self.assertTrue(s["speak_filler"])

    def test_think_with_filler_and_say_without_reply(self):
        s = pc.parse_surface("status: ok\ntone: casual_neutral\nmode: think\nreply: 我想想……")
        self.assertEqual(s["mode"], "think")
        self.assertTrue(s["speak_filler"])
        s2 = pc.parse_surface("status: ok\ntone: casual_neutral\nmode: say\nreply:")
        self.assertEqual(s2["mode"], "think")
        self.assertFalse(s2["speak_filler"])
        s3 = pc.parse_surface("status: not-for-me\ntone: casual_neutral\nmode: say\nreply:")
        self.assertEqual(s3["mode"], "say")      # silence, no deep call
        s4 = pc.parse_surface("status: unfinished\ntone: casual_neutral\nmode: say\nreply:")
        self.assertEqual(s4["mode"], "say")

    def test_missing_fields(self):
        s = pc.parse_surface("Yeah, I can hear you.")
        self.assertEqual(s["reply"], "Yeah, I can hear you.")
        self.assertEqual(s["mode"], "say")
        s2 = pc.parse_surface("status: not for me\ntone: casual_frustrated 0.5\nmode: Say + Think\nreply: は？\nfoo")
        self.assertEqual(s2["status"], "not-for-me")
        self.assertEqual(s2["tone"], "casual_frustrated")
        self.assertEqual(s2["mode"], "say+think")
        self.assertEqual(s2["reply"], "は？")

    def test_reply_is_one_line(self):
        s = pc.parse_surface("status: ok\ntone: casual_neutral\nmode: say\nreply: Yeah.\nAnd more text")
        self.assertEqual(s["reply"], "Yeah.")


class LanguageTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(pc.detect_lang("I told Raowin I prefer to decide"), "en")
        self.assertEqual(pc.detect_lang("你还记得上次我们聊的那首歌吗"), "zh")
        self.assertEqual(pc.detect_lang("ちょっと怒ってる演技してみて"), "ja")
        self.assertEqual(pc.detect_lang("東京 駅"), "zh")
        self.assertTrue(pc.is_kanji_only("東京駅"))
        self.assertIsNone(pc.detect_lang("... 123"))
        self.assertEqual(pc.detect_lang("你好世界再见朋友 hello ok"), "mixed")

    def test_reply_lang(self):
        self.assertEqual(pc.reply_lang("How do I say hello in Japanese?", "en"), ("ja", "switch"))
        self.assertEqual(pc.reply_lang("用日语说一遍", "zh"), ("ja", "switch"))
        self.assertEqual(pc.reply_lang("okay", "ja"), ("en", "detected"))
        self.assertEqual(pc.reply_lang("...", "ja"), ("ja", "inherited"))
        self.assertEqual(pc.reply_lang("...", None), (None, "unknown"))

    def test_language_line(self):
        self.assertIn("英语", pc.language_line("en"))
        self.assertIn("除非", pc.language_line("en"))
        self.assertIn("下一句再照对方自己说的语言来", pc.language_line("ja", "switch"))
        self.assertIn("最近在用的语言", pc.language_line(None, "unknown"))


class TemplateCheckTests(unittest.TestCase):
    # cache_p1p2 update (2026-10): the deep dynamic order is history BEFORE memories.
    TAIL = ("{daily_context}\n{current_scene}\n{current_time}\n{conversation_history}\n{memories_block}\n"
            "{pending_mind}\n{blurt_block}\n{say_marker}\n{deep_instruction}\n{language_line}\n")

    def _deep(self):
        vocab = "\n".join(pc.emotion_vocab_lines())
        sfx = " ".join(pc.SFX_NAMES)
        return (f"rules {pc.TSUNDERE_EXAMPLE}\n{vocab}\n{sfx}\n{{recent_mood}}\n{{current_action}}\n"
                "{relationship_context}\n{communication_style}\n" + self.TAIL)

    def test_good_deep(self):
        self.assertEqual(pc.check_template("deep", self._deep()), [])

    def test_bad_deep(self):
        text = self._deep().replace("{language_line}\n", "").replace("{conversation_history}\n{memories_block}", "{memories_block}\n{conversation_history}")
        text += "[模版]x {unknown_thing}"
        problems = pc.check_template("deep", text)
        self.assertTrue(any("missing placeholder {language_line}" in p for p in problems))
        self.assertTrue(any("must come before" in p for p in problems))
        self.assertTrue(any("unknown placeholder" in p for p in problems))
        self.assertTrue(any("[模版]" in p for p in problems))

    def test_vocab_mismatch_detected(self):
        text = self._deep().replace("whisper: ", "whisper: curious ")
        problems = pc.check_template("deep", text)
        self.assertTrue(any("whisper" in p and "extra=['curious']" in p for p in problems))

    def test_surface_recall_flagged(self):
        vocab = "\n".join(pc.emotion_vocab_lines())
        text = (f"{vocab}\n{' '.join(pc.SFX_NAMES)}\nrecall: x\n{{recent_mood}}{{current_action}}{{relationship_context}}"
                "{communication_style}{daily_context}{current_scene}{current_time}{memories_block}{conversation_history}{detected_lang}")
        self.assertTrue(any("recall" in p for p in pc.check_template("surface", text)))

    def test_split_and_leftovers(self):
        static, dynamic = pc.split_static("A {recent_mood} B {daily_context} C {memories_block}")
        self.assertEqual(static, "A {recent_mood} B ")
        self.assertTrue(dynamic.startswith("{daily_context}"))
        self.assertEqual(pc.leftover_placeholders("x {memories_block} {not_ours} {\"json\": 1}"), ["memories_block"])


class PerceivedTimeTests(unittest.TestCase):
    NOW = "2026-09-27 15:00:00"

    def test_buckets(self):
        cases = {
            "2026-09-27 14:50:00": "几分钟前", "2026-09-27 14:30:00": "半小时前", "2026-09-27 14:00:00": "一小时前",
            "2026-09-27 13:00:00": "两小时前的中午", "2026-09-27 12:00:00": "三小时前的中午", "2026-09-27 09:30:00": "几小时前的上午",
            "2026-09-27 03:00:00": "半天前的深夜", "2026-09-26 14:00:00": "一天前的下午", "2026-09-25 20:00:00": "两天前的晚上",
            "2026-09-24 18:00:00": "三天前的傍晚", "2026-09-22 07:00:00": "几天前的清晨", "2026-09-19 10:00:00": "一周前的上午",
            "2026-09-13 10:00:00": "两周前的上午", "2026-09-06 10:00:00": "三周前的上午", "2026-09-03 10:00:00": "几周前的上午",
            "2026-08-20 10:00:00": "一个月前的上午", "2026-07-30 10:00:00": "两个月前的上午", "2026-06-27 10:44:37": "三个月前的上午",
            "2026-05-20 10:00:00": "几个月前的上午", "2026-02-01 10:00:00": "半年前的上午", "2025-11-01 10:00:00": "一年前的上午",
            "2024-01-01 10:00:00": "一年多前的上午",
        }
        for when, want in cases.items():
            self.assertEqual(pc.when_phrase(when, self.NOW), want, when)

    def test_english_and_edge_cases(self):
        self.assertEqual(pc.when_phrase("2026-06-27 10:44:37", self.NOW, lang="en"), "three months ago in the morning")
        self.assertEqual(pc.when_phrase("2026-09-27 14:50:00", self.NOW, lang="en"), "a few minutes ago")
        self.assertEqual(pc.when_phrase("2026-06-27", self.NOW, date_only=True), "三个月前")
        self.assertEqual(pc.when_phrase("", self.NOW), "之前")
        self.assertEqual(pc.when_phrase("garbage", self.NOW), "之前")
        self.assertEqual(pc.when_phrase("2026-09-27 15:30:00", self.NOW), "刚刚")
        self.assertEqual(pc.time_of_day(pc.parse_when("2026-09-27 23:30:00")), "深夜")
        self.assertEqual(pc.perceived_ago("2026-09-27T14:50:00", self.NOW), "几分钟前")

    def test_speaker_names(self):
        self.assertEqual(pc.speaker_name("user", "Alex"), "Alex")
        self.assertEqual(pc.speaker_name("assistant", "Alex"), "我")
        self.assertEqual(pc.speaker_name("assistant", "Alex", lang="en"), "I")


if __name__ == "__main__":
    unittest.main()
