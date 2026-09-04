#!/usr/bin/env python3
"""智岗罗盘演示视频生成脚本（快速版）
PPT 幻灯片自动播放 + 淡入淡出转场 + 背景音乐
目标时长 ~9.5 分钟
使用 numpy 批量生成音频，ffmpeg 一次性合成
"""
import subprocess
import numpy as np
from pathlib import Path

SLIDES_DIR = Path(__file__).parent / "video_slides"
AUDIO_DIR = Path(__file__).parent / "video_audio"
OUTPUT = Path(__file__).parent / "智岗罗盘_初审演示.mp4"

AUDIO_DIR.mkdir(exist_ok=True)

NARRATIONS = [
    "大家好，这里是智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统。本项目面向科大讯飞挑战杯揭榜挂帅，由六人团队在两个月内完成全链路研发。",
    "技术迭代的速度，远超人才培养的周期。AI与大模型几乎每季度都会出现新方向，而一名工程师的培养需要数年，这造成了结构性的供需错配。",
    "针对这些痛点，我们提出了三项核心创新加一道工程护栏：动态演化、多源交叉验证、技能级人岗匹配，以及幻觉防控三道防线。",
    "系统采用五服务容器化架构，自上而下分为应用层、图谱算法层和采集层，Docker compose一键启动。",
    "采集层覆盖十三个数据源，按A、B、C三级分级管理，国内源直连，国际源走代理池，经过完整清洗管线确保入库质量。",
    "知识图谱是系统核心载体，在Neo4j中构建了岗位、技能、课程等多类实体与关系，五大骨干职能域由大模型语义命名。",
    "大模型抽取构建了幻觉防控三道防线：Schema强校验、词典过滤、证据链可追溯。一百一十条黄金集盲评F1达零点九六三二。",
    "人岗匹配引擎采用三维加权评分：技能、经验和学历，配合语义增强和通胀修正等纠偏机制。",
    "动态演化是差异化能力，Z-score加环比双信号检测追踪技能兴衰，技术热点观察池自动发现候选新岗位。",
    "系统生成个性化学习路径，先修链拓扑排序后匹配真实课程，三十案例专家评审合理性达百分之九十六点七。",
    "前端采用React 19加ECharts，默认2D力导向图百节点六十帧，3D模式动态加载，支持三视图切换与响应式适配。",
    "性能方面，一百并发下全景P95四百三十毫秒，搜索三百九十毫秒，远优于两秒目标。",
    "评测体系三层闭环：关键词基线、大模型盲审、专家定稿，四项核心指标全部达标。",
    "安全合规方面，简历脱敏、RBAC权限、限流中间件、数据采集遵循robots协议。",
    "系统已积累四千三百九十三个技能节点、一百四十个岗位、一千四百七十三门课程，黄金集支撑完整评测闭环。",
    "项目由六人团队协作完成，覆盖前端、后端、算法、数据、测试、文档全流程。",
    "项目历时五十五天，经历五个阶段，全程四百八十多个PR，CI全绿门禁。",
    "四项核心指标全部达标：JD解析零点九六三二、匹配零点九一四一、学习路径九六点七、性能P95四百三十毫秒。",
    "与传统方案相比，智岗罗盘在动态演化、多源验证、技能级匹配、学习路径闭环、可信防线五方面全面领先。",
    "智岗罗盘已实现全链路闭环，展望未来将推进新岗位自动发现与更多领域拓展。感谢各位评委的聆听。",
]

def calc_duration(text: str) -> float:
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    dur = cn_chars * 0.60 + 2.0  # 每字0.6秒 + 缓冲，语速放缓适配答辩
    return max(dur, 5.0)

def generate_bgm(output_path: Path, duration: float):
    """用 numpy 批量生成柔和背景环境音"""
    sr = 22050  # 降低采样率加快速度
    n = int(sr * duration)
    t = np.arange(n) / sr

    # 两个柔和正弦波 + 缓慢调制（模拟环境音）
    left = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.1 * t))
    right = 0.3 * np.sin(2 * np.pi * 330 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.15 * t))

    # 渐入渐出包络
    fade = min(1.5, duration * 0.05)
    fade_samples = int(fade * sr)
    envelope = np.ones(n)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    left *= envelope * 0.15  # 低音量
    right *= envelope * 0.15

    # 写 WAV
    import wave
    import struct
    with wave.open(str(output_path), 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        stereo = np.column_stack((left, right)).flatten()
        wf.writeframes((stereo * 32767).astype(np.int16).tobytes())


def main():
    print("=== 计算时长 ===")
    durations = [calc_duration(t) for t in NARRATIONS]
    total = sum(durations)
    for i, d in enumerate(durations, 1):
        cn = sum(1 for c in NARRATIONS[i-1] if '\u4e00' <= c <= '\u9fff')
        print(f"  Slide {i:02d}: {cn}字 → {d:.1f}s")
    print(f"总时长: {total:.1f}s ({total/60:.1f}min)")

    # 生成 BGM
    print("\n=== 生成背景音乐 ===")
    bgm_path = AUDIO_DIR / "bgm.wav"
    generate_bgm(bgm_path, total + 2)
    print(f"  ✓ BGM 生成完成 ({total+2:.1f}s)")

    # 用 ffmpeg concat demuxer + 直接从图片生成（不生成中间片段）
    print("\n=== 生成幻灯片视频 ===")
    # 写 concat 文件
    concat_file = AUDIO_DIR / "slides_concat.txt"
    with open(concat_file, "w") as f:
        for i in range(1, 21):
            img = SLIDES_DIR / f"slide-{i:02d}.png"
            dur = durations[i-1]
            f.write(f"file '{img}'\n")
            f.write(f"duration {dur}\n")
        # 最后一张图重复一次（ffmpeg concat 要求）
        f.write(f"file '{SLIDES_DIR / 'slide-20.png'}'\n")

    # 生成纯视频（无音频）- scale 确保宽高能被2整除
    video_raw = AUDIO_DIR / "video_raw.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vsync", "vfr",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        str(video_raw)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  错误: {r.stderr[-500:]}")
        return
    print("  ✓ 幻灯片视频生成完成")

    # 添加淡入淡出转场效果（用 xfade 滤镜）
    # 简化方案：首尾整体淡入淡出
    print("\n=== 添加转场效果 ===")
    video_faded = AUDIO_DIR / "video_faded.mp4"
    fade_in = 0.8
    fade_out = 0.8
    vf = f"fade=t=in:st=0:d={fade_in},fade=t=out:st={total-fade_out}:d={fade_out}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_raw),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(video_faded)
    ]
    subprocess.run(cmd, capture_output=True)
    print("  ✓ 转场效果添加完成")

    # 混合背景音乐
    print("\n=== 合成最终视频 ===")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_faded),
        "-i", str(bgm_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(OUTPUT)
    ]
    subprocess.run(cmd, capture_output=True)

    # 获取最终信息
    import json
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries",
         "format=duration:stream=width,height,codec_type",
         "-of", "json", str(OUTPUT)],
        capture_output=True, text=True
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
        print(f"   音频: AAC 128kbps 立体声")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
