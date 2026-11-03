"""TTS 情感 key 映射 —— Crescentine 离散情绪体系。

情绪用 `{语气域}_{情绪}` 命名（如 `casual_happy`），共 69 个离散值，**没有强度数字**。
LLM 先从 5 个语气域里挑一个，再挑一个情绪后缀，组合成 `register_emotion`。本模块把
LLM 给出的标签兜底成真实存在的 key。

OpenAmika：词表、别名、语气域内就近表和 resolve_emotion 都来自共享
契约模块 `po_contract`（大脑 brain/ 和发声端 speech/ 装的是同一个文件），这里只做转出，
保证两边对同一个标签得到同一个 key（契约 §1.4 / §4.1，parity test:
`tts_emotion.VALID_EMOTIONS == po_contract.VALID_EMOTIONS`）。

兜底策略 = **保语气域 + 语气域内就近映射**（po_contract.resolve_emotion）：
  0) 宽松写法规整：`Casual Happy` / `casual-happy` / `casual_shy 0.4` / `[casual_happy]`
  1) 别名疏导：LLM 爱写但 TTS 没有的词（curious/friendly…）先换成现有情绪概念
  2) 精确命中 `register_emotion` → 直接用
  3) `very_X` 且同语气域里有 `X` → 退到 `register_X`（同语气、降强度）
  4) 语气域内就近：该情绪在本语气域没有时，按 REGISTER_FALLBACK 映射到本域里最接近的情绪
  5) `register_neutral`（每个语气域都有 neutral，必命中）
  6) 空标签 / 认不出 → FALLBACK = `casual_neutral`（契约默认值；Build F 曾是 polite_neutral）

切角色（以后换 Amika 新版声音）时，改 po_contract 的 EMOTION_SETS + REGISTER_FALLBACK
两张表 + api.py 里的 TTS_CHARACTER_ID，其余逻辑不变。
"""

from typing import Optional

import po_contract
from po_contract import (  # noqa: F401  (re-exported module API)
    DEFAULT_EMOTION,
    EMOTION_ALIASES,
    REGISTER_FALLBACK,
    REGISTERS,
    VALID_EMOTIONS,
    _canon_emotion,
    _split_register,
    normalize_tag,
    resolve_emotion,
)

# 每个语气域下真实存在的情绪后缀（合计 69 个）。契约里是 tuple（保序，给模板词表用），
# 这里转成 set，保持本模块原来的类型。
EMOTION_SETS = {register: set(emotions) for register, emotions in po_contract.EMOTION_SETS.items()}

# 角色不带情绪标签 / 标签认不出时的兜底
FALLBACK = DEFAULT_EMOTION

_DEFAULT_REGISTER = "casual"


def prefix_emotion(emotion_key: Optional[str], text: str) -> str:
    """给一句话贴上情感前缀。emotion_key 为空就不贴（TTS 侧自动 fallback）。"""
    if not emotion_key:
        return text
    return f"【{emotion_key}】{text}"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    assert len(VALID_EMOTIONS) == 69, f"期望 69 个，实际 {len(VALID_EMOTIONS)}"
    assert VALID_EMOTIONS == po_contract.VALID_EMOTIONS, "tts_emotion 与 po_contract 词表不一致"
    assert {f"{r}_{e}" for r, es in EMOTION_SETS.items() for e in es} == set(VALID_EMOTIONS)
    assert FALLBACK == "casual_neutral" and FALLBACK in VALID_EMOTIONS
    cases = [
        ("casual_happy", "casual_happy"),              # 精确
        ("[casual_happy]", "casual_happy"),            # 带括号
        ("【whisper_sad】", "whisper_sad"),             # 带中文括号
        ("Casual Happy", "casual_happy"),              # 宽松写法
        ("casual_shy 0.4", "casual_shy"),              # 强度数字
        ("casual_very_shy", "casual_shy"),             # very_X 退 shy
        ("serious_very_angry", "serious_very_angry"),  # 精确（serious 有 very_angry）
        ("whisper_playful", "whisper_smile"),          # 就近
        ("whisper_laughing", "whisper_smile"),         # 就近
        ("whisper_angry", "whisper_worried"),          # 就近
        ("polite_disappointed", "polite_sad"),         # 就近
        ("serious_caring", "serious_protective"),      # 就近
        ("intimate_angry", "intimate_mumbling"),       # 就近
        ("casual_curious", "casual_smile"),            # 别名 curious->smile
        ("whisper_friendly", "whisper_smile"),         # 别名 friendly->smile
        ("casual_excited", "casual_very_happy"),       # 别名 excited->very_happy
        ("polite_excited", "polite_happy"),            # polite 无 very_happy -> polite_happy
        ("serious_disappointed", "serious_disappointed"),
        ("happy", "casual_happy"),                     # 没语气域 -> casual
        ("totally_made_up", "casual_neutral"),         # 乱来 -> casual_neutral
        ("", "casual_neutral"),                        # 空 -> FALLBACK
        (None, "casual_neutral"),
    ]
    bad_cases = 0
    for tag, want in cases:
        got = resolve_emotion(tag)
        mark = "" if got == want else f"   ✗ 期望 {want}"
        bad_cases += got != want
        print(f"{tag!r:>24} -> {got}{mark}")
    assert bad_cases == 0, f"{bad_cases} 个样例不符"

    # 不变式：任意语气域 × 任意情绪后缀 → 结果合法且仍在原语气域内
    all_suffixes = {e for es in EMOTION_SETS.values() for e in es}
    bad = 0
    for r in REGISTERS:
        for e in all_suffixes:
            res = resolve_emotion(f"{r}_{e}")
            if res not in VALID_EMOTIONS or not res.startswith(r + "_"):
                bad += 1
                print(f"  ✗ {r}_{e} -> {res}")
    print(f"\n不变式检查：{'全部通过' if bad == 0 else f'{bad} 条异常'}"
          f"（{len(REGISTERS)}×{len(all_suffixes)} 组合，结果均合法且保语气域）")
    assert bad == 0
    # REGISTER_FALLBACK 右值必须是该语气域真实有的情绪
    for r, m in REGISTER_FALLBACK.items():
        for src, dst in m.items():
            assert dst in EMOTION_SETS[r], f"{r}: {src}->{dst} 不在该语气域"
    print("REGISTER_FALLBACK 右值自检：全部落在各自语气域内")
    print(f"词表与 po_contract 一致：{len(VALID_EMOTIONS)} 个 key，FALLBACK={FALLBACK}")
