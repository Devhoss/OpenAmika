"""流式分句器（TTS 用）。

设计目标：
  - Hane 一段段流式推 chunk 过来，边收边切句。
  - 首句尽量短（用户一开口就能听到 AI 开始说话）。
  - 后续句子按 max_len 控制长度，不能过长也不能过碎。
  - 分句语义参考 `分句示例.py` 里的 TextSplitter，但预处理规则更激进：
    "被增强"的配对标点直接**替换**为分句标点（不保留原符），
    连续标点只保留最前面紧接文字的那一个，
    buffer 最前面若没有文字则标点全删。

预处理流程（对 buffer 整体跑，每一步都幂等，可以每次 feed 重复跑）：
  0. 缩写 / 小数点保护：把 Mr. / e.g. / 3.14 等里的 `.` 暂时换成占位符
     (U+2027)，避免被当成句末切分。产出 sentence 时 postprocess 再还原。
  1. punct_map 单字符替换：
       … -> 。
       ）)】]》> 及各种引号 -> ，
       （([【《 -> (删除)
       →←↑↓ -> ，
  2. 连续分句标点合并：一整串只保留首字符。
     例：……  -> 。。 -> 。；！？ -> ！；—— -> —。
  3. 若首句尚未产出，把 buffer 开头的标点串整个删掉（前面确实没文字）。

切分算法照抄作者思路：
  - pattern.split 成"文字块"/"标点块"交替。
  - 遇到标点块：
      * 含 end_chars（。！？.!?\n\r）-> 终止块；
        eff_len >= (首句 first_min_len / 非首句 min_len) 就切。
      * 否则（逗号、顿号、冒号等）-> 分隔块；
        eff_len >= (首句 first_max_len / 非首句 max_len) 才切。
  - "有效长度" = 除去所有标点后的字符宽度（ASCII=1，其他=2）。
  - final=True 时（flush），残留不管长度强制吐出。
"""

import re
from typing import List, Set, Pattern


class StreamingSplitter:
    def __init__(
        self,
        max_len: int = 40,
        min_len: int = 5,
        first_min_len: int = 2,
    ):
        """
        :param max_len:       非首句的软上限。达到此有效长度后遇任意分句标点就切。
        :param min_len:       非首句遇终止标点（句末）的最低有效长度。
        :param first_min_len: 首句统一用的阈值——首句一遇到任何标点（含逗号），
                              只要前面已有 >= first_min_len 个有效字符就切。
                              "首句尽量短"就靠这个：默认 2 ≈ 1 个汉字。
        """
        self.max_len = max_len
        self.min_len = min_len
        self.first_min_len = first_min_len

        # 终止字符（遇到就视为"句末"）
        self.end_chars: Set[str] = {
            '。', '！', '？',
            '!', '?', '.',
            '\n', '\r',
        }

        # 终止字符（补：日语半角句号 U+FF61）
        self.end_chars.add('\uff61')   # ｡

        # 全部分句标点（含终止字符 + 其他分隔符）。中日大部分标点（。！？，、；：）
        # 共用 Unicode 码点，已经覆盖。这里再补日语/半角的独有分隔符。
        self.splitting_puncts: Set[str] = self.end_chars | {
            # 中文
            '，', '、', '；', '：',
            '——', '—',
            # 英文
            ',', ';', ':',
            # 日语 / 半角片假名标点
            '\uff64',        # ､ 半角顿号
            # 其他结构性分隔
            '=', '+', '/', '×', '÷',
            # 注：人名连接号 `·`（U+00B7）和日语中点 `・`（U+30FB）**不**作为分句符。
            # 这两个常出现在 "哈利·波特" / "ハリー・ポッター" 里，切开会破坏人名。
            # 制表符也当分隔（换行已在 end_chars）
            '\t',
        }

        # 预处理映射表：直接把"被增强"的装饰性标点替换成对应分句标点。
        # 空串 '' 表示删除（左配对引导类标点）。
        self.punct_map = {
            # 省略号 -> 句号（最终强度由"连续标点合并"决定）
            '…': '。',
            # 右配对 -> 中文逗号（保留停顿，丢掉原符号）
            '）': '，', ')': ',',
            '】': '，', ']': ',',
            '》': '，', '>': ',',
            # 日语引号（右）-> 逗号
            '\u300d': '，',   # 」
            '\u300f': '，',   # 』
            # 双引号 -> 中文逗号
            '\u201c': '，',   # 左中引号 "
            '\u201d': '，',   # 右中引号 "
            '\u0022': ',',   # ASCII 双引号 "
            # 左单引号 -> 删除（右单引号 \u2019 另由 _re_apostrophe 处理撇号）
            '\u2018': '',
            # 左配对 -> 删除（引导符，不要停顿；避免"（你好）"变"，你好，"）
            '（': '', '(': '',
            '【': '', '[': '',
            '《': '',
            # 日语引号（左）-> 删除
            '\u300c': '',    # 「
            '\u300e': '',    # 『
            # 日语波浪号 -> 逗号（常用作"～さん"或语调延长，切开即可）
            '\u301c': '，',   # 〜
            '\uff5e': '，',   # ～ 全角波浪（另一个码点）
            # 箭头 -> 中文逗号
            '→': '，', '←': '，',
            '↑': '，', '↓': '，',
        }

        # 标点全集（有效长度时要跳过这些字符）
        # 这里补上 punct_map 的 key（装饰性），虽然预处理后应当已不存在；
        # 兜底防边界 case。
        self._all_puncts_chars: Set[str] = set(self.splitting_puncts) | set(self.punct_map.keys())

        # 编译正则
        # 多字符标点（如 "——"）要优先匹配，所以按长度降序
        sorted_puncts: List[str] = sorted(list(self.splitting_puncts), key=len, reverse=True)
        escaped = [re.escape(p) for p in sorted_puncts]
        punct_alt = '|'.join(escaped)
        # 用于把 buffer 切成"文字块"/"标点块"交替的 segments
        self._re_split: Pattern = re.compile(f"((?:{punct_alt})+)")
        # 匹配一整段连续分句标点（用于"连续只保留首字符"）
        self._re_run: Pattern = re.compile(f"(?:{punct_alt})+")
        # 匹配 buffer 开头的连续标点（用于首句未出时删前导）
        self._re_leading: Pattern = re.compile(f"^(?:{punct_alt})+")

        # 产出时做的"撇号归一"（I'm U+2019 → ASCII '），幂等；放到产出阶段避免动 buffer
        self._re_apostrophe: Pattern = re.compile(r"([a-zA-Z])\u2019([a-zA-Z])")

        # 缩写保护：把已知缩写里的 `.` 换成占位符，下游的分句就不会把它当句末。
        # 产出 sentence 时再还原。用 U+2027（HYPHENATION POINT），不在我们的分句集、
        # 也几乎不会出现在正常文本里。
        self._ABBREV_PH: str = '\u2027'
        # 常见英文缩写清单（按单词边界匹配；大小写敏感，避免误伤 mr/dr 当普通词）。
        # 每条匹配到的整个缩写里所有 `.` 都会被替换成占位符。
        self._re_abbrev: Pattern = re.compile(
            r'\b(?:'
            # 称谓 / 头衔
            r'Mr|Mrs|Ms|Mx|Dr|Prof|Sr|Jr|St|Ave|Blvd|Rd'
            # 常用缩写
            r'|vs|etc|cf|Co|Corp|Inc|Ltd|Gov|Gen|Rev|Hon|Capt|Sgt|Lt'
            # 时间（上下午）
            r'|a\.m|p\.m|A\.M|P\.M'
            # 拉丁缩写
            r'|e\.g|i\.e|E\.g|I\.e'
            # 地名缩写
            r'|U\.S\.A|U\.S|U\.K|E\.U|U\.N|N\.Y\.C|N\.Y|L\.A|D\.C'
            # 学位
            r'|Ph\.D|M\.D|B\.A|M\.A|B\.S|M\.S'
            r')\.'
        )
        # 数字小数点保护：`3.14` / `$1.99` / `v1.0` 之类；两侧都是数字才保护
        self._re_decimal: Pattern = re.compile(r'(\d)\.(\d)')

        # 流式状态
        self._buffer: str = ""
        self._first_emitted: bool = False

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        """新一轮 Hane 回复前调用，清空 buffer + 首句标志。"""
        self._buffer = ""
        self._first_emitted = False

    def feed(self, text: str) -> List[str]:
        """喂入新 chunk，返回这次能切出来的完整句子列表。"""
        if not text:
            return []
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> List[str]:
        """流结束。把 buffer 里还没切出去的残留强制吐出。"""
        return self._drain(final=True)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _char_width(ch: str) -> int:
        return 1 if ord(ch) < 128 else 2

    def _eff_len(self, s: str) -> int:
        """有效长度 = 除去所有标点后的字符宽度之和（ASCII=1，其他=2）。
        缩写保护占位符按"不计长度"处理（它代表的是被保护的 `.`，不是可朗读内容）。
        """
        n = 0
        for ch in s:
            if ch in self._all_puncts_chars:
                continue
            if ch == self._ABBREV_PH:
                continue
            n += self._char_width(ch)
        return n

    def _is_terminator_block(self, block: str) -> bool:
        """块里只要含一个终止字符就是终止块（句末级停顿）。"""
        for ch in block:
            if ch in self.end_chars:
                return True
        return False

    def _preprocess(self, text: str) -> str:
        """预处理：缩写保护 → punct_map 替换 → 连续标点合并 → 首句前导删除。幂等。"""
        if not text:
            return text
        # 0. 英文缩写 / 数字小数点里的 `.` 换成占位符，保护不被当句末
        text = self._re_abbrev.sub(
            lambda m: m.group(0).replace('.', self._ABBREV_PH), text,
        )
        text = self._re_decimal.sub(
            lambda m: m.group(1) + self._ABBREV_PH + m.group(2), text,
        )
        # 1. 装饰性/增强类标点直接映射
        for k, v in self.punct_map.items():
            if k in text:
                text = text.replace(k, v)
        # 2. 连续分句标点合并：整串 -> 首字符
        #    注意："——" 是 2 字符 token；对 "——" 运行时 m.group(0)="——"，m.group(0)[0]="—"，
        #    结果 "——" -> "—"，弱化为单破折号（按用户"只留最前面一个"的意图）
        text = self._re_run.sub(lambda m: m.group(0)[0], text)
        # 3. 首句未出 -> 掐掉 buffer 开头的标点/空白（前面真没文字）
        if not self._first_emitted:
            text = text.lstrip()
            text = self._re_leading.sub('', text)
        return text

    def _postprocess(self, sent: str) -> str:
        """产出一句时做的最终修饰：
          - 把缩写保护用的占位符还原回 `.`
          - 撇号归一（I'm 里的 Unicode 撇号 → ASCII）
        """
        if self._ABBREV_PH in sent:
            sent = sent.replace(self._ABBREV_PH, '.')
        return self._re_apostrophe.sub(r"\1'\2", sent)

    def _drain(self, final: bool) -> List[str]:
        if not self._buffer:
            return []
        self._buffer = self._preprocess(self._buffer)
        if not self._buffer:
            return []

        segments = self._re_split.split(self._buffer)
        out: List[str] = []
        cur = ""

        for seg in segments:
            if not seg:
                continue
            is_punct_block = seg[0] in self.splitting_puncts
            if is_punct_block:
                cur += seg
                eff = self._eff_len(cur)
                is_first = not self._first_emitted
                if is_first:
                    # 首句：任意标点（含逗号）都能切，只要超过 first_min_len
                    thr = self.first_min_len
                elif self._is_terminator_block(seg):
                    # 非首句、句末标点（。！？）：放宽到 min_len
                    thr = self.min_len
                else:
                    # 非首句、分隔标点（，、；：）：等到 max_len 再切
                    thr = self.max_len
                if eff >= thr:
                    sent = self._postprocess(cur.strip())
                    if sent:
                        out.append(sent)
                    cur = ""
                    self._first_emitted = True
            else:
                cur += seg

        if final and cur:
            stripped = cur.strip()
            if self._eff_len(stripped) > 0:
                sent = self._postprocess(stripped)
                if sent:
                    out.append(sent)
            cur = ""

        self._buffer = cur
        return out


# ---------------------------------------------------------------------------- #
# 简易自测（手工跑几个流式场景，直观看切分点）
# ---------------------------------------------------------------------------- #
if __name__ == "__main__":
    def demo(label: str, chunks: List[str]):
        print(f"\n=== {label} ===")
        sp = StreamingSplitter()
        for i, c in enumerate(chunks):
            outs = sp.feed(c)
            print(f"  feed[{i}]({c!r}) -> {outs}")
        outs = sp.flush()
        print(f"  flush()        -> {outs}")

    # 1. 逐字符流式
    demo("逐字符：你好，今天天气真不错。真的。",
         list("你好，今天天气真不错。真的。"))

    # 2. 较大 chunk
    demo("大块：长句+逗号+句号",
         ["虽然这个句子很短，", "但是下一句会很长，目的是为了测试在遇到逗号的时候，",
          "如果句子长度超过了我们设定的阈值，切分器是否能够正确地将其切断？应该没问题。"])

    # 3. 连续符号
    demo("连续符号：哎呀！！！真的吗？？？",
         ["哎呀！！！", "真的吗？？？"])

    # 4. 开头就是标点
    demo("开头标点：。！？这是一句",
         ["。！？", "这是一句，", "再来一句。"])

    # 5. 省略号
    demo("省略号：我...我不知道......",
         ["我......", "我不知道......"])

    # 6. 引号和括号
    demo("引号括号：她说\u201c你好\u201d。（小声）",
         ["她说\u201c你好\u201d。", "（小声）他走了。"])

    # 7. 英文撇号
    demo("英文撇号：I\u2019m fine.",
         ["I\u2019m fine.", " Don\u2019t worry about it."])

    # 8. 超短首句
    demo("超短首句：嗯。",
         ["嗯", "。好的。"])

    # 9. 英文缩写
    demo("英文缩写 Mr./Dr.",
         ["I met Mr. ", "Smith and Dr. Jones today."])

    # 10. 拉丁缩写 e.g./i.e.
    demo("拉丁缩写 e.g. / i.e.",
         ["For example, e.g. apples, ", "pears, etc. And i.e. it is good."])

    # 11. 地名和时间缩写
    demo("U.S. / a.m.",
         ["The U.S. meeting is at 9 a.m. today. ", "Be there on time."])

    # 12. 小数点和版本号
    demo("小数点 3.14 / v1.0",
         ["Pi is 3.14 and version is v1.0. ", "It's very precise."])

    # 13. 跨 chunk 切在缩写中间
    demo("跨 chunk 切在 Mr 和 . 之间",
         ["Please call Mr", ". Smith about the report."])
