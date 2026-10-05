#!/usr/bin/env python3
"""扫描创意工坊壁纸清单 —— 包内 picker 与 start-wallpaper.sh 共用。

两边都需要「遍历工坊目录、读 project.json、按类型和标题排序」，过去各写了
一份，改格式或排序时很容易只改一边，出现「界面里有的壁纸 --list 里没有」
这类不一致。现在只留这一份实现。

命令行用法（start-wallpaper.sh 调用，写 TSV 清单）:
    python3 wallpaper_picker/scan.py <workshop目录> <输出文件>

Python 调用（wallpaper_picker.picker 使用）:
    from .scan import scan_workshop
"""

import glob
import json
import os
import sys

# 类型排序：场景最常用放最前，解析失败的排最后
ORDER = {"scene": 0, "video": 1, "web": 2}


def _classify(meta, wid):
    """从 project.json 判定壁纸类型。

    特殊情况：无 type 但有 dependency 的是「预设包」——它是对某个
    依赖壁纸的参数配置（渲染器读不到 type 会直接抛错），标成 preset
    让界面给出明确提示而不是含混的"启动失败"。
    """
    title = str(meta.get("title", wid)).strip()
    wtype = str(meta.get("type", "")).lower()
    if not wtype:
        wtype = "preset" if meta.get("dependency") else "?"
    return title, wtype


def scan_workshop(workshop):
    """返回 [(wtype, wid, title), ...]，已按类型和标题排序。

    project.json 读不出来（下载中断、格式怪异）的壁纸也要列出来，
    类型标为 "?"，让用户知道它存在只是坏了，而不是凭空消失。
    """
    rows = []
    for path in sorted(glob.glob(os.path.join(workshop, "*/"))):
        pj = os.path.join(path, "project.json")
        if not os.path.exists(pj):
            continue
        wid = os.path.basename(path.rstrip("/"))
        try:
            # utf-8-sig：部分作者的 project.json 带 BOM
            meta = json.load(open(pj, encoding="utf-8-sig"))
            title, wtype = _classify(meta, wid)
        except Exception:
            title, wtype = f"{wid}（配置无法解析）", "?"
        rows.append((wtype, wid, title))
    rows.sort(key=lambda r: (ORDER.get(r[0], 9), r[2]))
    return rows


def _write_tsv(rows, out_path):
    with open(out_path, "w", encoding="utf-8") as fh:
        for wtype, wid, title in rows:
            fh.write(f"{wid}\t{wtype}\t{title}\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    _rows = scan_workshop(sys.argv[1])
    _write_tsv(_rows, sys.argv[2])
    print(f"扫描到 {len(_rows)} 张壁纸")
