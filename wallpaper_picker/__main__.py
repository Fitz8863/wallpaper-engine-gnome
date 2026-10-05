"""命令行入口 —— python -m wallpaper_picker 与根目录薄壳共用。

参数解析保持在 picker.__main__ 语义（--snapshot/--select/--restore），
本模块只做转发，不吞参数——薄壳/包入口行为必须完全一致。
"""

import sys

from . import picker


def main(argv=None):
    return picker.run_app(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
