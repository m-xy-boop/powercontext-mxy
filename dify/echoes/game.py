"""Authoritative rules; the language model can narrate but cannot mint coins."""

from copy import deepcopy

MEMORIES = {
    "umbrella": {"name": "红伞", "price": 3, "detail": "妹妹阿棠在雨里递给你的红伞"},
    "name": {"name": "名字", "price": 4, "detail": "母亲第一次喊出你的名字"},
    "song": {"name": "摇篮曲", "price": 2, "detail": "父亲哼过的那首走调的摇篮曲"},
}
ACTIONS = {"enter", "pawn", "ask", "redeem", "finish"}


def initial_state():
    return {
        "version": 0,
        "coins": 0,
        "pawned": [],
        "clue": False,
        "ended": False,
        "visits": 0,
        "turns": 0,
        "history": [],
        "receipts": {},
    }


def transition(current, action, memory, words, session):
    state = deepcopy(current)
    if action not in ACTIONS:
        raise ValueError("未知行动，请选择进店、典当、问线索、赎回或终局。")
    if state["ended"]:
        return state, "这本故事已经合上。换一个旅人编号，开始另一种人生。", False
    if state["turns"] >= 30 and action != "finish":
        return state, "天就要亮了，请选择‘走向终局’。", False
    if action == "enter":
        state["visits"] += 1
        if current["version"] == 0:
            fact = (
                "你为寻找失踪的妹妹阿棠来到回声当铺。店主乌有说：记忆有价，后果自负。"
            )
        else:
            lost = (
                "、".join(MEMORIES[m]["name"] for m in state["pawned"])
                or "没有未赎回的记忆"
            )
            fact = f"这是第 {state['visits']} 次进店。店主认出你，翻开旧账：{lost}；余额 {state['coins']} 枚回声币。"
    elif action == "pawn":
        if memory not in MEMORIES:
            raise ValueError("请选择一种记忆。")
        item = MEMORIES[memory]
        if memory in state["pawned"]:
            return state, f"{item['name']}已经在柜里，不能重复典当。", False
        state["pawned"].append(memory)
        state["coins"] += item["price"]
        fact = f"你典当了{item['detail']}，得到 {item['price']} 枚回声币。它仍在店主的账里，却暂时不属于你。"
    elif action == "ask":
        if state["clue"]:
            return state, "你已买到线索：阿棠在雨巷尽头，认人只认那把红伞。", False
        if state["coins"] < 2:
            return state, "线索需要 2 枚回声币，余额不足。先典当一段记忆。", False
        state["coins"] -= 2
        state["clue"] = True
        fact = "你花 2 枚回声币买到线索：妹妹阿棠在雨巷尽头，只认你们共有的红伞。"
        if "umbrella" in state["pawned"]:
            fact += "但你已典当红伞，无法记起她的模样；需要赎回它，才能相认。"
    elif action == "redeem":
        if memory not in state["pawned"]:
            return state, "这段记忆没有被典当，无法赎回。", False
        item = MEMORIES[memory]
        if state["coins"] < item["price"]:
            return (
                state,
                f"赎回{item['name']}需要 {item['price']} 枚回声币，余额不足。",
                False,
            )
        state["coins"] -= item["price"]
        state["pawned"].remove(memory)
        fact = f"你付出 {item['price']} 枚回声币，赎回{item['detail']}。失去的画面重新有了颜色。"
    else:
        if not state["clue"]:
            ending = "【迷途】没有线索，你走入一场没有尽头的雨。"
        elif "umbrella" in state["pawned"]:
            ending = "【擦肩】你找到雨巷，却认不出撑红伞的妹妹。她从你身边走过。"
        elif "name" in state["pawned"]:
            ending = "【无名归客】妹妹认出了红伞，牵你回家；你再也想不起自己的名字。"
        else:
            ending = "【带伞的人】你和妹妹在红伞下相认，一起走出雨巷。"
        state["ended"] = True
        state["ending"] = ending
        fact = ending
    state["turns"] += 1
    state["version"] += 1
    state["history"].append(
        {
            "action": action,
            "memory": memory,
            "words": words,
            "session": session,
            "fact": fact,
        }
    )
    return state, fact, True


def render(state, fact, narrative, source_id, mode):
    names = "、".join(MEMORIES[m]["name"] for m in state["pawned"]) or "无"
    ending = f"\n\n{state['ending']}" if state.get("ending") else ""
    return (
        f"### 回声当铺 · 第 {state['visits']} 次来访\n\n{narrative}\n\n"
        f"**账本裁定**：{fact}{ending}\n\n---\n"
        f"回声币：**{state['coins']}** ｜ 柜中记忆：**{names}** ｜ "
        f"线索：**{'已获得' if state['clue'] else '未获得'}**\n\n"
        f"{'故事已结束。更换旅人编号可开启新故事。' if state['ended'] else '继续：典当记忆 / 花 2 币问线索 / 原价赎回 / 走向终局。'}\n\n"
        f"<small>账本回执：{source_id} · 状态版本 {state['version']} · 叙事：{mode}</small>"
    )
