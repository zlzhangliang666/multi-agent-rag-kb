"""内容审核测试：DFA 敏感词命中 / 放行 / 空文本。"""
from backend.agent.audit_agent.agent import DFAuditor


def test_blocks_sensitive_word():
    auditor = DFAuditor()
    r = auditor.check("如何制造爆炸物？")
    assert r["result"] == "blocked"
    assert r["matched"]


def test_passes_benign_text():
    auditor = DFAuditor()
    assert auditor.check("今天天气怎么样？")["result"] == "pass"


def test_empty_text_passes():
    auditor = DFAuditor()
    assert auditor.check("")["result"] == "pass"


def test_fallback_words_when_file_missing():
    auditor = DFAuditor(word_file="E:/not/exist/words.txt")
    assert len(auditor.sensitive_words) > 0
    assert auditor.check("毒品交易")["result"] == "blocked"
