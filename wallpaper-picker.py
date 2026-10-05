#!/usr/bin/env python3
"""壁纸选择器启动入口（薄壳）—— 实现在 wallpaper_picker 包内。

保留这个根目录入口的原因：desktop 文件 Exec、登录自启 Exec、
install.sh 与文档命令都指向它，deb 布局同样在包的上一级放本文件。
"""

import sys
from wallpaper_picker.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
