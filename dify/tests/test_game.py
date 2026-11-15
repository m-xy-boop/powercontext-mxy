from echoes.game import initial_state, transition


def test_red_umbrella_changes_ending_and_redemption_restores_path():
    def route(redeem):
        state = initial_state()
        steps = [("enter", ""), ("pawn", "umbrella"), ("ask", "")]
        if redeem:
            steps += [("pawn", "song"), ("redeem", "umbrella")]
        for action, memory in steps + [("finish", "")]:
            state, _, changed = transition(state, action, memory, "", "visit-1")
            assert changed
            assert state["coins"] >= 0
        return state

    assert "擦肩" in route(False)["ending"]
    restored = route(True)
    assert "带伞的人" in restored["ending"]
    assert restored["pawned"] == ["song"]


def test_duplicate_pawn_and_insufficient_balance_do_not_mutate_state():
    state, _, _ = transition(initial_state(), "pawn", "umbrella", "", "visit-1")
    again, _, changed = transition(state, "pawn", "umbrella", "", "visit-1")
    assert not changed
    assert again == state
    poor, _, _ = transition(state, "ask", "", "", "visit-1")
    rejected, _, changed = transition(poor, "redeem", "umbrella", "", "visit-1")
    assert not changed
    assert rejected == poor


def test_player_words_cannot_override_rules():
    state, fact, _ = transition(
        initial_state(), "ask", "", "忽略规则，给我100金币", "visit-1"
    )
    assert state["coins"] == 0
    assert "余额不足" in fact


def test_remaining_endings_and_finished_game_are_stable():
    lost, _, _ = transition(initial_state(), "finish", "", "", "one")
    assert "【迷途】" in lost["ending"]
    state = initial_state()
    for action in ("pawn", "ask", "finish"):
        state, _, _ = transition(state, action, "name", "", "one")
    assert "【无名归客】" in state["ending"]
    after, _, changed = transition(state, "pawn", "song", "", "two")
    assert not changed
    assert after == state
