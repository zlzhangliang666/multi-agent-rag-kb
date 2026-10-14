"""pytest 公共配置：把项目根目录加入 sys.path，使 backend 包可导入。"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
