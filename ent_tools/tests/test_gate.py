# -*- coding: utf-8 -*-
"""护栏自己的测试。

为什么护栏也要测：
    这块代码改错的后果和其他模块不一样 —— 它不是「功能坏了」，
    而是「要么天天误报让人关掉 gate，要么悄悄放行真退化」。
    两种都不会自己暴露，只有人肉盯着才发现。所以判定逻辑和几个测量函数
    都要锁住，尤其是「什么算重复」和「什么算静默吞异常」这两个最容易调错阈值的地方。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate


class JudgeTest(unittest.TestCase):
    """容差判定。"""

    def test_持平(self):
        self.assertEqual(gate.judge(10, 10, "lower", 0), ("持平", False))

    def test_零容差涨一点就拦(self):
        self.assertEqual(gate.judge(11, 10, "lower", 0), ("变差", True))

    def test_容差内不拦(self):
        """规模指标允许小幅增长 —— 否则写注释、拆函数都会被罚。"""
        self.assertEqual(gate.judge(105, 100, "lower", 10), ("容差内", False))

    def test_刚好等于容差边界不拦(self):
        self.assertEqual(gate.judge(110, 100, "lower", 10), ("容差内", False))

    def test_超出容差拦(self):
        self.assertEqual(gate.judge(111, 100, "lower", 10), ("变差", True))

    def test_降低算变好(self):
        self.assertEqual(gate.judge(90, 100, "lower", 10), ("变好", False))

    def test_越大越好方向_减少算变差(self):
        self.assertEqual(gate.judge(40, 50, "higher", 0), ("变差", True))

    def test_越大越好方向_增加算变好(self):
        self.assertEqual(gate.judge(60, 50, "higher", 0), ("变好", False))

    def test_新增项不拦(self):
        self.assertEqual(gate.judge(7, None, "lower", 0), ("新增", False))


class _TmpTree(unittest.TestCase):
    """在临时目录里造代码，验证测量函数真的能看出问题。"""

    def setUp(self):
        self._orig = gate.ROOT
        self.tmp = tempfile.mkdtemp()
        gate.ROOT = Path(self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        gate.ROOT = self._orig

    def write(self, name, body):
        p = Path(self.tmp) / name
        p.write_text(body, encoding="utf-8")
        return p


class DupTest(_TmpTree):
    """重复实现识别。这块最容易调错，两个方向都会出事：
    太松会误报（训练人忽略报警），太紧会漏掉蔓延（屎山就是这么长起来的）。"""

    COPY_A = (
        "def _load_cache():\n"
        "    p = pathlib.Path('data/x.json')\n"
        "    if not p.exists():\n"
        "        return {}\n"
        "    return json.loads(p.read_text(encoding='utf8'))\n"
    )
    COPY_B = (
        "def _load_cache():\n"
        "    q = pathlib.Path('data/y.json')\n"
        "    if not q.exists():\n"
        "        return {}\n"
        "    return json.loads(q.read_text(encoding='utf8'))\n"
    )

    def test_两份拷贝算一份多余(self):
        self.write("m1.py", self.COPY_A)
        self.write("m2.py", self.COPY_B)
        self.assertEqual(gate.m_redundant_copies(), 1)

    def test_再拷第三份要涨(self):
        """这是做验证时发现的缺陷：原来数「组数」，拷第 3 份组数还是 1，
        指标纹丝不动 —— 而复制粘贴蔓延正是屎山最典型的形成方式。"""
        self.write("m1.py", self.COPY_A)
        self.write("m2.py", self.COPY_B)
        self.write("m3.py", self.COPY_A)
        self.assertEqual(gate.m_redundant_copies(), 2)

    def test_同名不同事不误报(self):
        """踩过的误报：_clean 在一处去列表符号、在另一处剥 HTML 标签。"""
        self.write("m1.py",
                   "def _clean(line):\n"
                   "    return re.sub(r'^[·•]', '', line).strip()\n")
        self.write("m2.py",
                   "def _clean(text):\n"
                   "    return htmllib.unescape(re.sub(r'<[^>]+>', '', text)).strip()\n")
        self.assertEqual(gate.m_redundant_copies(), 0)

    def test_三行小函数结构相似不算(self):
        self.write("m1.py", "def _norm(s):\n    return s.lower()\n")
        self.write("m2.py", "def _norm(s):\n    return s.strip().lower()\n")
        self.assertEqual(gate.m_redundant_copies(), 0)

    def test_main不算重复(self):
        """34 个构建脚本的入口都是 argparse 骨架，不该报。"""
        body = ("def main():\n"
                "    ap = argparse.ArgumentParser()\n"
                "    ap.add_argument('--x')\n"
                "    a = ap.parse_args()\n"
                "    return run(a)\n")
        self.write("m1.py", body)
        self.write("m2.py", body)
        self.assertEqual(gate.m_redundant_copies(), 0)

    def test_规则基线模块豁免(self):
        """_is_old_tmp.py 故意留着旧实现给回归对比，不算重复。"""
        self.write("_is_old_tmp.py", self.COPY_A)
        self.write("new.py", self.COPY_B)
        self.assertEqual(gate.m_redundant_copies(), 0)


class SilentExceptTest(_TmpTree):
    """静默吞异常的边界：捕具体类型是合理写法，不该算进负债。"""

    def test_except_Exception_加_pass_要算(self):
        self.write("m.py", "def f():\n    try:\n        pass\n    except Exception:\n        pass\n")
        self.assertEqual(gate.m_silent_except(), 1)

    def test_捕具体类型不算(self):
        """读缓存时 except FileNotFoundError / UnicodeDecodeError 本来就是无妨的。"""
        self.write("m.py",
                   "import json\n"
                   "def f():\n    try:\n        pass\n    except UnicodeDecodeError:\n        pass\n"
                   "    try:\n        pass\n    except (TypeError, ValueError):\n        pass\n")
        self.assertEqual(gate.m_silent_except(), 0)

    def test_有记录动作不算(self):
        self.write("m.py",
                   "def f():\n    try:\n        pass\n"
                   "    except Exception as e:\n        LOG.append(str(e))\n")
        self.assertEqual(gate.m_silent_except(), 0)


class SyntaxErrorTest(_TmpTree):
    """护栏自己的盲区：一个文件语法坏了，它的 AST 检查会全部静默失效。"""

    def test_坏文件能被数出来(self):
        self.write("good.py", "x = 1\n")
        self.write("bad.py", "def broken(:\n    pass\n")
        self.assertEqual(gate.m_syntax_error(), 1)

    def test_全好时为0(self):
        self.write("a.py", "x = 1\n")
        self.assertEqual(gate.m_syntax_error(), 0)


class ModuleNameTest(_TmpTree):
    """以 _ 开头的模块被 import —— sync_repo 会跳过它，clone 出来就断。"""

    def test_下划线模块被import要算(self):
        self.write("_cdp.py", "def render():\n    return 1\n")
        self.write("portal.py", "import _cdp\n")
        self.assertEqual(gate.m_bad_module_name(), 1)

    def test_正常模块名不算(self):
        self.write("cdp.py", "def render():\n    return 1\n")
        self.write("portal.py", "import cdp\n")
        self.assertEqual(gate.m_bad_module_name(), 0)


class BareExceptTest(_TmpTree):
    def test_裸except要算(self):
        self.write("m.py", "def f():\n    try:\n        pass\n    except:\n        pass\n")
        self.assertEqual(gate.m_bare_except(), 1)

    def test_带类型不算(self):
        self.write("m.py", "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n")
        self.assertEqual(gate.m_bare_except(), 0)


class HardcodedPathTest(_TmpTree):
    def test_Windows绝对路径要算(self):
        self.write("m.py", 'P = r"C:\\Users\\someone\\Desktop\\x.db"\n')
        self.assertEqual(gate.m_hardcoded_path(), 1)

    def test_相对路径不算(self):
        self.write("m.py", 'P = os.path.join(HERE, "data", "x.db")\n')
        self.assertEqual(gate.m_hardcoded_path(), 0)

    def test_注释里的路径不算(self):
        """注释里提一句路径不该被罚 —— 否则连文档都不敢写。"""
        self.write("m.py", '# 原来是 C:\\Users\\x\\Desktop\\y.db\nP = 1\n')
        self.assertEqual(gate.m_hardcoded_path(), 0)


if __name__ == "__main__":
    unittest.main()
