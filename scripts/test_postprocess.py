"""Quick demo for postprocess module."""

from douyin_to_text.postprocess import correct_transcript, get_active_backend

TITLE = "MrBeast 末日地堡"
DESCRIPTION = (
    "世界最最神秘最坚固的末日核地堡 价值超过10亿美元的世界最昂贵核地堡。"
    "从1美元的地堡到最贵地堡应有尽有#野兽先生 #mrbeast #末日地堡"
)

RAW = (
    "这座大山里面用藏地一座造家十亿美元的多地宝它能够给预游是以来最大原子弹的冲击"
    "在学下来的视频中会向你们展示造架五千万美元的地宝以及其他各种地宝"
    "首先要介绍这个一美元的地宝安全 进里面看看好嘘 我带你们参观一下这个地宝"
    "好的 看完了说它是地宝 其实就是个被埋起来的集装箱这已经变形了"
    "你觉得它能抵离炸弹吗不行试试才知道 投下着它等等 什么后面的地宝会越来越厉害"
    "去下一座吧接下来是造下一百万的地宝它最初其实是一座草弹发射警户"
    "为于地地深处可以用来地域合摩日"
)


def main() -> None:
    print("=== 当前后端 ===")
    print(get_active_backend())
    print()
    print("=== 原始 ASR ===")
    print(RAW)
    print()
    print("=== 后处理输出 ===")
    result = correct_transcript(TITLE, DESCRIPTION, RAW)
    print(result)


if __name__ == "__main__":
    main()
