#!/usr/bin/env python3
"""智岗罗盘演示视频生成脚本（旁白版）
PPT 幻灯片自动播放 + 逐页 edge-tts 旁白 + 背景音乐
每页时长由旁白音频真实时长驱动，目标总时长 ~7-8 分钟
依赖：uvx edge-tts、ffmpeg、numpy
"""
import subprocess
import sys
import numpy as np
from pathlib import Path

SLIDES_DIR = Path(__file__).parent / "video_slides"
AUDIO_DIR = Path(__file__).parent / "video_audio"
OUTPUT = Path(__file__).parent / "智岗罗盘_初审演示.mp4"

TTS_VOICE = "zh-CN-YunyangNeural"
TTS_RATE = "-6%"
PAD_SEC = 0.9

AUDIO_DIR.mkdir(exist_ok=True)

# 19 页旁白，口径与 PPT 画面逐位一致（10 域 / 0.9632 / 0.9714 / 96.7% / graph_v20260903）
NARRATIONS = [
    "各位评委好。这里是智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统。本项目面向科大讯飞挑战杯揭榜挂帅，由六人团队在五十五天内完成全链路研发，下面从设计、实现到评测逐页汇报。",
    "先看行业背景。技术迭代速度远超人才培养周期，课程体系天然滞后；新兴产业招聘难与毕业生就业难并存；岗位描述存在更新滞后、内容抄袭、要求通胀三大顽疾；而传统人岗匹配大多停留在关键词比对，难以刻画真实能力。",
    "针对这些痛点，我们提出三项核心创新：一是基于图谱的岗位能力动态演化，二是多源数据交叉验证，三是技能级人岗匹配与差距诊断；并以大模型幻觉防控三道防线作为工程护栏，保证结论可追溯、可复现。",
    "系统采用五服务容器化架构。应用层由FastAPI统一提供接口，前端构建产物同端口托管；图谱算法层承载Neo4j图谱、pgvector向量与匹配演化引擎；采集层完成多源抓取。全部服务由Docker Compose一键启动。",
    "这是端到端全流程：多源采集、清洗去重后进入原始库，累计入库一万七千二百五十条；再经大模型结构化抽取构建图谱，快照规模两万二千五百七十节点、七万七千零六十五边；最终支撑匹配、演化、学习路径三大引擎与前端三视图。",
    "数据采集覆盖十三个数据源，按A、B、C三级分级管理，国内源直连、国际源走代理池；清洗管线执行去重、正则抽取、语言检测与质量过滤四步；全程遵循robots协议。实况累计入库一万七千二百五十条，其中JD一万一千一百一十五条。",
    "知识图谱以Neo4j 5为载体，构建岗位、技能、课程、论文、社区五类实体与四类关系：岗位要求技能关系一万一千七百余条，课程学习关系九千七百余条，证据支撑关系四万七千九百余条；十大职能域由大模型语义命名，归类依据逐岗可查。",
    "大模型抽取环节构建了幻觉防控三道防线：Schema强校验、词典过滤、证据链追溯，每次决策全量落库、可回放审计。一百一十条黄金集双盲评审，对齐F1零点九六三二，幻觉误报率低于百分之一。",
    "人岗匹配引擎采用技能、经验、学历三维加权评分，支持Top-N排名与单点比对双模式；引入SBERT语义增强缓解措辞差异，并设计了通胀修正、时效衰减、跨域降权等纠偏机制，输出技能级的三态差距诊断。",
    "动态演化是系统的差异化能力。Z-score加环比MoM双信号检测新兴与衰退技能，岗位状态机追踪新兴、稳定、衰退生命周期；技术热点观察池汇聚arXiv与GitHub信号，自动发现候选新岗位，经人工审核后入图。",
    "系统将差距诊断转化为个性化学习路径：以缺失与薄弱技能为输入，结合二百四十二条先修链技能字典做拓扑排序，通过课程学习关系匹配真实课程并做质量门控，最终以甘特图呈现。三十个案例专家评审，路径合理性达百分之九十六点七。",
    "前端基于React 19加ECharts实现三大视图：全景视图2D力导向布局，百节点稳定六十帧；技术栈视图聚合技能到岗位的关联热度；岗位画像以雷达图呈现单岗位能力要求。支持深浅主题与响应式适配，3D模式可选并自动降级。",
    "性能方面，一百并发压测下，全景图谱接口P95四百三十毫秒，搜索接口三百九十毫秒，均远优于两秒目标；通过single-flight合并、缓存与连接池优化，缓存命中率达百分之八十五以上。",
    "评测体系采用三层闭环：关键词基线提供下界，大模型双盲评审提供独立参照，专家定稿形成权威标签。四项核心指标全部达标，其中JD解析F1零点九六三二，匹配准确率零点九七一四，均超越预设目标。",
    "安全合规贯穿设计全程：简历等个人信息入库前脱敏，接口采用RBAC三级权限与双令牌鉴权，限流中间件防护恶意请求，数据采集遵循robots协议并附学术用途声明，全链路可审计。",
    "这是系统的实况数据快照：七千三百四十二个技能节点、一百一十八个岗位、一千七百五十四门课程，JD原始表一万一千一百一十五条；图谱快照版本v2026-09-03，累计两万二千五百七十节点、七万七千零六十五条边。",
    "关键成果一览：JD解析对齐F1零点九六三二，匹配准确率零点九七一四，学习路径合理性百分之九十六点七，技能分类视图覆盖百分之九十一点七；全程四百八十多个PR、约七点一万行代码变更，CI全绿六轮审查。",
    "与传统方案相比，智岗罗盘在五个维度形成差异化：动态能力画像替代静态标签，多源交叉验证替代单源可信，技能级匹配替代关键词命中，学习路径形成后续动作闭环，三道防线保障大模型结论可信。",
    "总结：智岗罗盘在五十五天内打通了从数据采集、图谱构建到人岗匹配、演化发现与学习路径的全链路闭环，四项核心指标全部达标。后续将推进新岗位自动发现与领域扩展。演示系统已在线部署，感谢各位评委聆听！",
]


def tts_slide(idx: int, text: str, out: Path) -> float:
    cmd = [
        "uvx", "edge-tts",
        "--voice", TTS_VOICE,
        f"--rate={TTS_RATE}",
        "--text", text,
        "--write-media", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"TTS 失败 slide {idx:02d}: {r.stderr[-300:]}")
    r2 = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    )
    return float(r2.stdout.strip())


def pad_to_wav(src: Path, dst: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-af", f"apad=pad_dur={PAD_SEC}",
         "-ar", "24000", "-ac", "2", str(dst)],
        capture_output=True, check=True,
    )


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def generate_bgm(output_path: Path, duration: float):
    """用 numpy 批量生成柔和背景环境音"""
    sr = 22050
    n = int(sr * duration)
    t = np.arange(n) / sr

    left = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.1 * t))
    right = 0.3 * np.sin(2 * np.pi * 330 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.15 * t))

    fade = min(1.5, duration * 0.05)
    fade_samples = int(fade * sr)
    envelope = np.ones(n)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    left *= envelope * 0.15
    right *= envelope * 0.15

    import wave
    with wave.open(str(output_path), 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        stereo = np.column_stack((left, right)).flatten()
        wf.writeframes((stereo * 32767).astype(np.int16).tobytes())


def main():
    n_slides = len(NARRATIONS)
    pngs = sorted(SLIDES_DIR.glob("slide-*.png"))
    if len(pngs) != n_slides:
        print(f"页数不一致：video_slides 有 {len(pngs)} 张 PNG，NARRATIONS 有 {n_slides} 条")
        sys.exit(1)

    print(f"=== 逐页 TTS 旁白（{TTS_VOICE}，语速 {TTS_RATE}）===")
    durations = []
    for i, text in enumerate(NARRATIONS, 1):
        mp3 = AUDIO_DIR / f"slide_{i:02d}.mp3"
        wav = AUDIO_DIR / f"slide_{i:02d}.wav"
        raw = tts_slide(i, text, mp3)
        pad_to_wav(mp3, wav)
        dur = probe_duration(wav)
        durations.append(max(dur, 6.0))
        print(f"  Slide {i:02d}: {len(text)}字 raw {raw:.1f}s → {durations[-1]:.1f}s")
    total = sum(durations)
    print(f"总时长: {total:.1f}s ({total/60:.1f}min)")

    print("\n=== 拼接旁白音轨 ===")
    concat_a = AUDIO_DIR / "narration_concat.txt"
    with open(concat_a, "w", encoding="utf-8") as f:
        for i in range(1, n_slides + 1):
            f.write(f"file 'slide_{i:02d}.wav'\n")
    narration_wav = AUDIO_DIR / "narration.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_a),
         "-c", "copy", str(narration_wav)],
        capture_output=True, check=True,
    )
    print(f"  ✓ 旁白音轨 {probe_duration(narration_wav):.1f}s")

    print("\n=== 生成背景音乐 ===")
    bgm_path = AUDIO_DIR / "bgm.wav"
    generate_bgm(bgm_path, total + 1)
    print(f"  ✓ BGM {total+1:.1f}s")

    print("\n=== 生成幻灯片视频 ===")
    concat_file = AUDIO_DIR / "slides_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for i in range(1, n_slides + 1):
            f.write(f"file '{SLIDES_DIR / f'slide-{i:02d}.png'}'\n")
            f.write(f"duration {durations[i-1]}\n")
        f.write(f"file '{SLIDES_DIR / f'slide-{n_slides:02d}.png'}'\n")

    video_raw = AUDIO_DIR / "video_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-vsync", "vfr",
         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
         "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         str(video_raw)],
        capture_output=True, check=True,
    )
    print("  ✓ 幻灯片视频生成完成")

    print("\n=== 首尾淡入淡出 ===")
    video_faded = AUDIO_DIR / "video_faded.mp4"
    fade_in = 0.8
    fade_out = 0.8
    vf = f"fade=t=in:st=0:d={fade_in},fade=t=out:st={total-fade_out:.2f}:d={fade_out}"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_raw), "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-pix_fmt", "yuv420p", str(video_faded)],
        capture_output=True, check=True,
    )
    print("  ✓ 转场效果添加完成")

    print("\n=== 合成最终视频（旁白 + 背景音）===")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video_faded), "-i", str(narration_wav), "-i", str(bgm_path),
         "-filter_complex",
         "[2:a]volume=0.35[b];[1:a][b]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
         "-shortest", str(OUTPUT)],
        capture_output=True, check=True,
    )

    import json
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries",
         "format=duration:stream=width,height,codec_type",
         "-of", "json", str(OUTPUT)],
        capture_output=True, text=True,
    )
    info = json.loads(result.stdout)
    dur = float(info["format"]["duration"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    size_mb = OUTPUT.stat().st_size / 1024 / 1024

    print(f"\n{'='*50}")
    print(f"✅ 演示视频生成完成!")
    print(f"   文件: {OUTPUT.name}")
    print(f"   时长: {dur:.1f}s ({dur/60:.1f}min)")
    print(f"   分辨率: {v['width']}x{v['height']}")
    print(f"   大小: {size_mb:.1f} MB")
    if a:
        print(f"   音频: AAC 128kbps 立体声（旁白 + 背景音）")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
