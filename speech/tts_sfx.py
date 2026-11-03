"""声效（SFX）标签解析与按语气选片。

LLM 在台词里用 `【Sigh】` 这种标签插入非语言声效（笑/叹气/打哈欠…），插在句子之间。
它只认 9 个**通用**声效名，不知道声效其实分语气域——分语气这件事我们在播放前
自己处理：

  1) 看这条声效**前面最近一句台词**的情绪 key（如 `casual_happy`）→ 取语气域 `casual`
  2) 在该语气域下找同名声效文件 `Casual_<name>_SFX.wav`
  3) 该语气域没有这个声效时 → **保声音含义**：按语气邻近顺序借最近的一个语气域
     （Serious 没有 Laugh → 退到 Polite/Casual… 第一个有 Laugh 的；笑还是笑，
      语气稍飘一点，但绝不把"笑"换成"叹气"）
  4) 这个声效所有语气域都没有 → 返回 None（丢弃，不出声）

文件命名约定：`<Register>_<SfxName>_SFX.wav`，放在 STT/static/sfx/。
增删音效文件不用改代码——本模块在导入时扫描目录自建可用矩阵。

2026-07-27（Amika_JA_v2 交付）：现役声效集换成日文命名、无语气域的
`<声效名>_SFX_<变体>.wav`（挂通配域 "*"）；LLM 标签仍是英文 9 选 1，经
SFX_ALIASES 映射到日文文件。旧集完整保留在 static/sfx_v1_SakuraiMika_rollback/，
回滚 = 两个目录对调 + 无需改代码。

OpenAmika（契约 §1.5 / §4.4）：标签名一律先过共享契约
`po_contract.canonical_sfx`（9 个名字的闭合词表 + 少量别名，大小写/空格宽松）。
模型照抄的占位符（【声效名】【register_emotion】【tag】）和不认识的名字直接丢弃、
绝不朗读。规整后的英文名再走下面的 SFX_ALIASES 映射到日文文件。
标签的形状与契约 `strip_sfx` 一致（po_contract._ANY_SFX_LIKE_RE：`【` + 1~24 个字 + `】`）。
"""

import random
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from po_contract import _ANY_SFX_LIKE_RE, canonical_sfx

# 5 个语气域（与 tts_emotion 一致）
REGISTERS = ("polite", "casual", "serious", "intimate", "whisper")
_DEFAULT_REGISTER = "casual"

# 声效缺失时借语气域的邻近顺序（第一个=自己）。思路同 tts_emotion 的语气邻近：
# 正式↔正式、亲近↔亲近优先靠拢。
REGISTER_PRIORITY = {
    "serious":  ["serious", "polite", "casual", "intimate", "whisper"],
    "polite":   ["polite", "serious", "casual", "intimate", "whisper"],
    "casual":   ["casual", "intimate", "polite", "whisper", "serious"],
    "intimate": ["intimate", "casual", "whisper", "polite", "serious"],
    "whisper":  ["whisper", "intimate", "casual", "polite", "serious"],
}

SFX_DIR = Path(__file__).resolve().parent / "static" / "sfx"

# 文件名解析：<Register>_<SfxName>_SFX.wav 或 <Register>_<SfxName>_SFX_<变体>.wav
#   （变体后缀如 _var1 / _v2 / _2，可有可无；大小写不敏感地认 Register 前缀）
# name 用贪婪 .+ 回溯到最后一个 _SFX，能正确切出含下划线的声效名（Can_t_help_laughing 等）。
_FNAME_RE = re.compile(
    r'^(?P<reg>[A-Za-z]+)_(?P<name>.+)_SFX(?:_[A-Za-z0-9]+)?\.wav$',
    re.IGNORECASE,
)

# 新命名（Amika_JA_v2 交付 2026-07-27）：<声效名>_SFX_<变体>.wav —— 没有语气域
# 前缀、声效名是日文（ため息_SFX_2.wav）。这类文件挂在通配语气域 "*" 下，
# resolve 走完语气邻近链后兜底取它。旧命名（带 Register 前缀）继续支持，
# 把 sfx_v1 目录原样放回来就能回滚。
_FNAME_FREE_RE = re.compile(
    r'^(?P<name>.+)_SFX(?:_[A-Za-z0-9]+)?\.wav$',
    re.IGNORECASE,
)

# LLM 的标签词汇表保持**英文**（人设 表层.txt/例子.txt 的"9 选 1"，契约
# po_contract.SFX_NAMES），所以文件名换成日文后在这里做 英文标签 → 日文文件名 的别名映射。
# key 是契约规整名的小写。只在目录里真的有对应日文文件时才生效：回滚回英文命名的旧目录后
# 别名自动失效。
SFX_ALIASES = {
    "laugh": "笑う",
    "can_t_help_laughing": "思わず笑う",
    "soft_hmph": "小さくふん",
    "sigh": "ため息",
    "deep_breath": "深呼吸",
    "yawn": "あくび",
    "sniff": "鼻をすする",
    "light_cough": "軽く咳払い",
    "throat_clear": "咳払い",
}


def _scan() -> Tuple[dict, dict, list]:
    """扫描 SFX_DIR，返回：
      AVAILABLE_FILES: {(register_lower, sfx_lower): ["文件名.wav", ...]}  同声效多变体收成列表
      CANON:           {sfx_lower: "原始大小写的 SfxName"}（给提示词/规整用）
      FILES:           ["原始文件名.wav", ...] 排序（含全部变体，给前端预加载清单）
    """
    available: Dict[Tuple[str, str], List[str]] = {}
    canon: dict = {}
    files: list = []
    if not SFX_DIR.is_dir():
        return available, canon, files
    for p in sorted(SFX_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".wav":
            continue
        m = _FNAME_RE.match(p.name)
        if m and m.group("reg").lower() in REGISTERS:
            reg = m.group("reg").lower()
            name = m.group("name")
        else:
            m = _FNAME_FREE_RE.match(p.name)   # 无语气域的新命名 → 通配域 "*"
            if not m:
                continue
            reg = "*"
            name = m.group("name")
        name_l = name.lower()
        available.setdefault((reg, name_l), []).append(p.name)
        canon.setdefault(name_l, name)
        files.append(p.name)
    return available, canon, files


AVAILABLE_FILES, _CANON, FILES = _scan()

# 生效的别名（小写英文标签 → 目录里真实存在的声效名小写）；目标不存在则不映射。
_ALIAS_L = {en: ja.lower() for en, ja in SFX_ALIASES.items() if ja.lower() in _CANON}

# 目录里实际存在的声效名（给前端/自检看；LLM 词表以 po_contract.SFX_NAMES 为准）
VALID_SFX = sorted(_CANON.values())


def list_files() -> List[str]:
    """给前端预加载用的文件清单。"""
    return list(FILES)


def register_of(emotion_key: Optional[str]) -> str:
    """从情绪 key（如 `casual_happy`）取语气域。空/认不出 → 默认 casual。"""
    if not emotion_key:
        return _DEFAULT_REGISTER
    head = emotion_key.split("_", 1)[0].lower()
    return head if head in REGISTERS else _DEFAULT_REGISTER


def _file_key(name: Optional[str]) -> Optional[str]:
    """LLM 标签 → 契约规整名（9 选 1）→ 目录里的声效名小写 key。不认识/占位符/目录里没有 → None。"""
    canon = canonical_sfx(name)
    if not canon:
        return None
    key = canon.lower()
    key = _ALIAS_L.get(key, key)
    return key if key in _CANON else None


def canon_sfx(name: Optional[str]) -> Optional[str]:
    """把标签名规整成契约的标准写法（`Laugh` / `Can_t_help_laughing` …）；不认识返回 None。
    只看契约词表，不看目录里有没有文件（播放时 resolve_sfx 再选文件）。"""
    return canonical_sfx(name)


def resolve_sfx(name: Optional[str], emotion_key: Optional[str]) -> Optional[str]:
    """按"前一句情绪的语气域 + 保声音含义"选出要播的声效文件名。
    选定语气域后，若该声效有多个变体，**随机挑一个**。

    名字先过 po_contract.canonical_sfx（占位符/不认识 → None），再映射到目录里的声效。
    返回 `static/sfx/` 下的文件名；声效名不认识、或所有语气域都没有该声效 → None。
    """
    key = _file_key(name)
    if key is None:
        return None
    register = register_of(emotion_key)
    for reg in REGISTER_PRIORITY.get(register, REGISTER_PRIORITY[_DEFAULT_REGISTER]):
        variants = AVAILABLE_FILES.get((reg, key))
        if variants:
            return random.choice(variants)
    # 无语气域的声效（JA v2 命名）：语气链走完后兜底通配域
    variants = AVAILABLE_FILES.get(("*", key))
    return random.choice(variants) if variants else None


# 抽取器 → api 之间用的"声效 delta"哨兵：speech 流里普通文本是裸字符串，
# 声效则把名字裹上这个前缀，api 侧识别后转成播放队列里的声效项。
SFX_DELTA_PREFIX = "\x00SFX:"


def make_sfx_delta(name: str) -> str:
    return SFX_DELTA_PREFIX + name


def parse_sfx_delta(delta: str) -> Optional[str]:
    """delta 是声效哨兵则返回声效名，否则返回 None。"""
    if delta and delta.startswith(SFX_DELTA_PREFIX):
        return delta[len(SFX_DELTA_PREFIX):]
    return None


# ---------------- 流式标签分离 ---------------- #
# 台词流里识别 `【SfxName】`，把它从要朗读的文本里切出来作为独立声效事件。
# 标签形状与契约 strip_sfx 相同（_ANY_SFX_LIKE_RE）；名字过 canonical_sfx，只认 9 个契约
# 名字；其它 `【...】`（占位符、中文动作、不认识的名字）静默丢弃、不朗读，原位置补一个
# 空格（与 strip_sfx 一致，避免英文单词被粘在一起）。
# 不成形的 `【`（没有收尾 / 里面超过 24 个字）按普通文字处理，与契约一致。
_OPEN = "【"
_CLOSE = "】"


class SfxScanner:
    """对一条"要朗读的台词文本流"做流式扫描，分离出声效标签。

    feed/flush 返回一批二元组：
      ("text", 文本片段)  —— 正常台词，继续走分句器/TTS
      ("sfx",  规整声效名) —— 一个声效（契约名，如 `Laugh`），调用方据此强制分句 + 入队播放

    跨 chunk 安全：看到 `【` 但还判断不了它是不是标签时把这段悬住，等后续 chunk 再判。
    """

    def __init__(self):
        self._buf = ""

    def reset(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> List[Tuple[str, str]]:
        if not text:
            return []
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> List[Tuple[str, str]]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        buf = self._buf
        pos = 0
        text_start = 0
        while True:
            i = buf.find(_OPEN, pos)
            if i == -1:
                break
            m = _ANY_SFX_LIKE_RE.match(buf, i)
            if m:
                if i > text_start:
                    out.append(("text", buf[text_start:i]))
                canon = canonical_sfx(m.group(1))
                if canon:
                    out.append(("sfx", canon))
                else:
                    out.append(("text", " "))   # 占位符 / 不认识：丢弃，不朗读
                pos = text_start = m.end()
                continue
            # 还没成形：后面补 `】`（或补一个字再 `】`）就能匹配 = 后续 chunk 还可能把它补成标签 → 悬住
            if (not final and _CLOSE not in buf[i:]
                    and (_ANY_SFX_LIKE_RE.match(buf[i:] + _CLOSE) or _ANY_SFX_LIKE_RE.match(buf[i:] + "x" + _CLOSE))):
                if i > text_start:
                    out.append(("text", buf[text_start:i]))
                self._buf = buf[i:]
                return out
            pos = i + 1                            # 普通的 `【`，当文字
        if text_start < len(buf):
            out.append(("text", buf[text_start:]))
        self._buf = ""
        return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"SFX_DIR = {SFX_DIR}")
    print(f"扫描到 {len(FILES)} 个文件，{len(VALID_SFX)} 种声效：{VALID_SFX}")
    print("\n可用矩阵（语气 × 声效，数字=变体数；* = 无语气域通配）：")
    _cols = REGISTERS + ("*",)
    header = "                      " + "  ".join(f"{r[:4]:>4}" for r in _cols)
    print(header)
    for name_l in sorted(_CANON):
        row = [f"{_CANON[name_l]:<20}  "]
        for r in _cols:
            n = len(AVAILABLE_FILES.get((r, name_l), []))
            row.append(f" {n if n else '—'}  ".rjust(6))
        print("".join(row))

    print("\nresolve 示例（前一句情绪 → 声效 → 选中文件；多变体连选 3 次看随机）：")
    cases = [
        ("Can_t_help_laughing", "casual_happy"),  # casual 有 4 变体
        ("Sigh", "casual_neutral"),               # casual 有 2 变体
        ("Sigh", "whisper_sleepy"),               # whisper 无 Sigh → 借 intimate(单)
        ("Sigh", "serious_cold"),                 # serious 无 Sigh → 借 polite(2变体)
        ("Deep_breath", "whisper_sad"),           # whisper 有 2 变体
        ("Light_cough", "casual_happy"),          # 仅 polite，3 变体
        ("Throat_clear", "serious_cold"),         # 仅 serious，单
        ("Yawn", "casual_tired"),                 # casual 无 → 借 intimate
        ("sigh", None),                           # 大小写不敏感 + 默认 casual
        ("Nonexistent", "casual_happy"),          # 不认识 → None
        ("声效名", "casual_happy"),                # 占位符 → None
    ]
    for name, emo in cases:
        picks = [resolve_sfx(name, emo) for _ in range(3)]
        print(f"  {name:>20} @ {str(emo):>16} -> {picks}")

    # 不变式：5 语气 × 9 个契约声效名 都能解析出真实存在的文件（目录存在时）
    from po_contract import SFX_NAMES
    bad = 0
    if FILES:
        for r in REGISTERS:
            for sfx_name in SFX_NAMES:
                res = resolve_sfx(sfx_name, f"{r}_neutral")
                if res is None or res not in FILES:
                    bad += 1
                    print(f"  ✗ {r} × {sfx_name} -> {res}")
        print(f"\n不变式：5×9 组合 {'全部解析到真实文件' if bad == 0 else f'{bad} 条异常'}")
    else:
        print("\n不变式：本机没有 static/sfx 目录，跳过文件矩阵检查")

    print("\nSfxScanner 流式：")
    sc = SfxScanner()
    chunks = ["哈哈，太好笑", "了。【Can_t", "_help_laughing】不行", "我缓缓。【Yawn】", "困了。【声效",
              "名】好【laugh】。【笑】嗯"]
    for c in chunks:
        print(f"  feed({c!r}) -> {sc.feed(c)}")
    print(f"  flush() -> {sc.flush()}")
    assert canon_sfx("声效名") is None and canon_sfx("register_emotion") is None
    assert canon_sfx("laugh") == "Laugh" and canon_sfx("throat clear") == "Throat_clear"
    assert resolve_sfx("声效名", "casual_happy") is None
