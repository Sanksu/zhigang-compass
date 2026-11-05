# -*- coding: utf-8 -*-
"""实机演示视频合成：13 镜片段 + 旁白 + 字幕烧录 + 拼接 + BGM（0905）。

口径依据 docs/m5/演示脚本_口播稿.md 与录制执行档_20260903.md；
字幕文本即口播稿逐镜旁白，按标点切块、时长占比铺时间轴。
"""
import os, re, subprocess, shutil, json
from pathlib import Path

ROOT = Path(r"C:\Users\87088\Desktop\zglp\zhigang-compass")
AUD = ROOT / "docs/m5/video_audio"
SHOTS = AUD / "shots"
SEG = AUD / "segments"
SLIDES = ROOT / "docs/m5/video_slides"
OUT = ROOT / "docs/m5/智岗罗盘_实机演示_20260905.mp4"
FONT_DIR = "C:/Windows/Fonts"
# 字幕：白色文字 + 黑色描边（BorderStyle=1 描边式）
STYLE = "FontName=Microsoft Ya Hei,FontSize=12,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=26"
# 无 TTS 后各镜目标时长改由旁白 wav 实测时长驱动（DUR 仅作兜底）
DUR = {1: 19.5, 2: 43.2, 3: 37.3, 4: 38.3, 5: 58.8, 6: 37.8,
       7: 34.9, 8: 26.5, 9: 60.0, 10: 21.8, 11: 40.4, 12: 46.5, 13: 25.4}
# 镜 14 额外片尾：报告渲染后停留供阅读
EXTRA_TAIL = {14: 11.5}
LEAD = 1.2   # 旁白起点相对片段头的静默
TAIL = 1.2

NARR = {
1: "「智岗罗盘」——多源异构驱动的岗位能力动态演化与人岗匹配系统。本项目面向科大讯飞挑战杯·揭榜挂帅，由六人团队在两个月内完成全链路研发。",
2: "技术迭代的速度，远超人才培养的周期——AI 与大模型几乎每个季度都会出现新方向，而一名工程师的培养需要数年。这造成了供需错配：企业招不到合适的人，求职者不知道该学什么。招聘岗位描述又普遍存在三个问题：沿用过时的要求、相互抄袭、虚高门槛。而传统的人岗匹配大多停留在关键词命中，缺乏技能级的差距诊断和个性化学习路径。",
3: "系统采用五服务容器化架构：API、PostgreSQL 向量库、Redis、Neo4j 图数据库，以及 ARQ 异步任务 Worker。数据链路清晰：采集层汇聚多源数据，经过图数据库和算法引擎，最终通过统一 API 与前端呈现。我们登录进入管理首页，可以看到图谱规模、采集量、版本快照等实时总览。",
4: "采集层覆盖十三个数据源，按 A、B、C 三级分级管理。国内源直连，国际源走代理池随机轮换、失败剔除。每条数据经过清洗管线：长度过滤、质量评分、SimHash 去重、时效加权。我们切到「调度与限频」标签，可以看到每个爬虫的开关、采集上限和独立触发时间，实现一处管全的采集治理。",
5: "进入能力图谱，这是系统核心的可视化。默认 2D 力导向图，数百节点保持流畅交互。全景视图里，全部岗位按职能域聚合常驻——十个职能域：前端、后端、算法等五个大的骨干域，再加上网络安全、AI 应用与智能体、企业应用与系统、产品与项目管理这四个细分的语义域，以及一个通用兜底域，每个岗位的归类依据都可逐岗核查。双击域展开域内岗位，再双击展开岗位技能。我们依次切换三种视图：全景、技术栈、岗位画像；再用答辩视角书签，一键飞达锚定岗位。3D 视图按需动态加载，WebGL2 不可用时自动降级 2D。",
6: "接下来演示核心功能——人岗匹配。我们上传一份技术简历，系统进行三维加权评分：技能、经验、学历。匹配结果按综合得分给出推荐 Top-N 列表。注意这里每个岗位展示的，是对齐到最匹配那条真实 JD 的分数——推荐列表与岗位详情同源同分，可逐位对账；语义增强让技能表达更鲁棒。",
7: "点击一个岗位进入详情，这里展示任职要求与必备技能。关键的差距分析把候选人与岗位逐项对比：哪些是必备缺失、哪些是熟练度不足，并按需求度、趋势、ROI 三指标排出优先级。每条差距都可展开「JD 原文」溯源——证据链可回指原始招聘描述，体现了幻觉防控与可解释性。",
8: "针对差距，系统生成个性化学习路径。它按先修关系展开技能字典，进行拓扑排序，并匹配图谱中的真实课程，灰带技能还有质量门控兜底。时间轴以甘特图呈现分阶段的学时安排，点击即可跳转对应课程。",
9: "再看驱动的源头——动态演化。演化看板分三个标签：信号、趋势与流向、版本与状态机。「信号」用 Z-score 的占比口径检测新兴与衰退技能 Top-10，抗规模波动；「趋势与流向」展示技能频次变化与流向桑基图——桑基按非空期次排列，蓝、绿、橙三色区分持续在榜、新进榜与末次在榜，底部标注期次日期，技术观察池还持续接收 arXiv、GitHub 等社区学术信号；「版本与状态机」支持多快照对比，并展示岗位生命周期状态机与演化谱系。告警与关键指标跨标签始终置顶。",
10: "演化的另一个输出是新岗位发现。结合技术观察池的 arXiv、GitHub 热点信号，这里列出近期涌现的岗位候选及其技能，并对比旧岗位的技能增减，让「新方向在哪里」一目了然。",
11: "系统的多源抽取由大模型完成，我们构建了幻觉防控三道防线：Schema 强校验、词典过滤、证据链可追溯。管理后台的「LLM 决策与验收」把六域决策全量透明化、可回放；「动态别名表」实现技能归一的自治治理；字典守卫拦截非法或多义技能；「原始数据」页则把 JD、课程、论文、社区信号四类原始数据统一治理——全链路可审计。",
12: "最后看效果。评测体系用一百一十条正式黄金集做 LLM 盲审：技能对齐口径 F1 达到 0.9632，满足 0.90 达标线；简历抽取 F1 0.988，学习路径合理性 96.7%，匹配在 BT v4 权重、含学历维度下准确率 0.9714。性能方面，100 并发下全景与搜索的 P95 分别为 430 与 390 毫秒，远优于 2 秒目标。以上数据以打包前最新快照为准。",
13: "「智岗罗盘」已实现从采集、图谱、抽取、匹配、演化到学习路径的全链路闭环，并用可追溯的证据链守护可信。展望未来，我们将推进新岗位的自动发现，并向更多领域拓展。感谢各位评委的聆听。",
14: "匹配结果的最下方，还有一份 AI 诊断报告。大模型综合匹配得分、技能差距与学习路径，自动生成总体解读、维度分析与改进建议，每条建议都附证据追溯入口。一键生成，结果缓存二十四小时，可随时刷新。",
}

# 镜号 → (类型, 源, [入点, 出点])
SH = {
1:  ("slide", SLIDES / "slide-01.png"),
2:  ("slide", SLIDES / "slide-02.png"),
3:  ("video", SHOTS / "page@c1ca250e0f2daafe6eee0863ac229a96.webm", None),
4:  ("video", SHOTS / "page@5d73356cd6459c56efea957e97c0ef1b.webm", None),
5:  ("video", SHOTS / "page@8a580016c5d405fd2555a6613a3e25a0.webm", (0, 61.5)),
6:  ("video", SHOTS / "page@18cafa7ffe406b7581e961d3429fcfc6.webm", None),
7:  ("video", SHOTS / "page@7b0c21ba5a606c4113174607fb2970da.webm", (33.0, 70.3)),
8:  ("video", SHOTS / "page@7b0c21ba5a606c4113174607fb2970da.webm", (83.0, 111.5)),
9:  ("video", SHOTS / "shot9_spliced.webm", None),
10: ("video", SHOTS / "page@8fd37fe8a9044bcf64a3f19fc0190833.webm", None),
11: ("video", SHOTS / "page@646771d44746a1967c5e5b8cec6e52a8.webm", None),
12: ("slide", SLIDES / "slide-19.png"),
13: ("slide", SLIDES / "slide-19.png"),
14: ("video", SHOTS / "page@2ee18a94b5aa29c51097148703e80ad9.webm", (66.0, 107.0)),
}

# 段序：镜 14（AI 诊断报告）插在学习路径之后
ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 14, 9, 10, 11, 12, 13]

def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip())

def wav_dur(path):
    return probe(path)

# 字幕整句同显：按句末标点切句，句内按逗号/顿号手动换行（每行 ≤ LINE_MAX）
def chunk_text(text, linemax=20):
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；])", text) if s.strip()]
    out = []
    for sent in sentences:
        lines, cur = [], ""
        tokens = re.split(r"(?<=[，、：；])", sent)
        for tok in tokens:
            while len(tok) > linemax:
                lines.append(cur + tok[:linemax])
                tok = tok[linemax:]
                cur = ""
            if len(cur) + len(tok) > linemax and cur:
                lines.append(cur)
                cur = tok
            else:
                cur += tok
        if cur:
            lines.append(cur)
        out.append("\n".join(lines))
    return out

def write_srt(idx, text, narr_dur):
    chunks = chunk_text(text)
    total_chars = sum(len(c) for c in chunks)
    lines = []
    t = LEAD
    for i, c in enumerate(chunks):
        dur = narr_dur * len(c) / total_chars
        start, end = t, min(t + dur, LEAD + narr_dur)
        ts = lambda s: f"{int(s//3600):02d}:{int(s%3600//60):02d}:{int(s%60):02d},{int(s%1*1000):03d}"
        lines.append(f"{i+1}\n{ts(start)} --> {ts(end)}\n{c}\n")
        t = end
    (SEG / f"shot_{idx:02d}.srt").write_text("\n".join(lines), encoding="utf-8")

def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])

def main():
    if SEG.exists():
        shutil.rmtree(SEG)
    SEG.mkdir(parents=True)
    seg_files = []
    for idx in ORDER:
        wav = AUD / "tts" / f"shot_{idx:02d}.wav"
        nd = wav_dur(wav)
        target = LEAD + nd + TAIL + EXTRA_TAIL.get(idx, 0)
        kind = SH[idx][0]
        vf = f"fps=30,format=yuv420p"
        # 去齿音：deesser 主消 + 7kHz 窄带衰减兜底
        af = f"[1:a]adelay={int(LEAD*1000)}|{int(LEAD*1000)},deesser=i=0.5:m=0.4:f=0.5,equalizer=f=7000:t=q:w=2:g=-4,aresample=48000[a]"
        srt = SEG / f"shot_{idx:02d}.srt"
        write_srt(idx, NARR[idx], nd)
        sub = f"subtitles=filename=shot_{idx:02d}.srt:force_style='{STYLE}'"
        out = SEG / f"seg_{idx:02d}.mp4"
        if kind == "slide":
            png = SH[idx][1]
            cmd = ["ffmpeg", "-y", "-v", "error",
                   "-loop", "1", "-framerate", "30", "-i", str(png), "-i", str(wav),
                   "-filter_complex",
                   f"[0:v]{vf},tpad=stop_mode=clone:stop_duration={target+2:.2f},{sub}[v];{af}",
                   "-map", "[v]", "-map", "[a]", "-t", f"{target:.2f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", "-b:a", "128k", str(out)]
        else:
            src = SH[idx][1]
            rng = SH[idx][2]
            src_dur = probe(src)
            need = target
            pre = ["-ss", str(rng[0]), "-t", str(min(rng[1]-rng[0], need + 1))] if rng else []
            have = min(rng[1]-rng[0], src_dur - (rng[0] if rng else 0)) if rng else src_dur
            pad = max(0.0, need - have)
            cmd = ["ffmpeg", "-y", "-v", "error"] + pre + ["-i", str(src), "-i", str(wav),
                   "-filter_complex",
                   f"[0:v]{vf},tpad=stop_mode=clone:stop_duration={pad+1:.2f},{sub}[v];{af}",
                   "-map", "[v]", "-map", "[a]", "-t", f"{need:.2f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", "-b:a", "128k", str(out)]
        print(f"seg {idx:02d} encoding... target={target:.1f}s")
        run(cmd, cwd=SEG)
        seg_files.append(out)
        print(f"seg {idx:02d} ok -> {out.name}")
    # 拼接
    lst = SEG / "concat.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in seg_files), encoding="utf-8")
    joined = SEG / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(joined)])
    # 拼接后人声（各段已含去齿音旁白）+ BGM 低音量混音
    bgm = AUD / "bgm.wav"
    joined_dur = probe(joined)
    if bgm.exists():
        fc = (f"[0:a]aresample=48000[na];"
              f"[1:a]aresample=48000,volume=0.06,atrim=0:{joined_dur:.2f},afade=t=out:st={joined_dur-3:.2f}:d=3[bg];"
              f"[na][bg]amix=inputs=2:duration=first:dropout_transition=0[a]")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(joined),
             "-stream_loop", "-1", "-i", str(bgm),
             "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-shortest",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", str(OUT)])
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(joined), "-c", "copy",
             "-movflags", "+faststart", str(OUT)])
    print("FINAL:", OUT, probe(OUT), "s")

if __name__ == "__main__":
    main()
