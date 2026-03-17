#!/usr/bin/env python3
"""Liar's Dice — play against MCCFR bot locally. Supports 1v1 and 2v2."""

import sqlite3
import random
import json
import os
from flask import Flask, render_template, jsonify, request, session

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Game mode configs
MODES = {
    '1v1': {
        'db': 'strategy_1v1.db',
        'dice_per_player': 1,
        'total_dice': 2,
        'label': '1 vs 1',
    },
    '2v2': {
        'db': 'strategy_2v2.db',
        'dice_per_player': 2,
        'total_dice': 4,
        'label': '2 vs 2',
    },
}


def get_all_bids(total_dice):
    """All valid bids for a given total number of dice."""
    bids = []
    for qty in range(1, total_dice + 1):
        for face in range(2, 7):
            bids.append(f"{qty}x{face}")
    return bids


def get_all_hands(dice_per_player):
    """All possible hands (sorted ascending) for a given number of dice."""
    if dice_per_player == 1:
        return [str(i) for i in range(1, 7)]
    else:
        hands = []
        for i in range(1, 7):
            for j in range(i, 7):
                hands.append(f"{i},{j}")
        return hands


def get_mode(req):
    """Get mode from request args, default to 2v2."""
    mode = req.args.get('mode') or (req.get_json() or {}).get('mode') or '2v2'
    if mode not in MODES:
        mode = '2v2'
    return mode


def get_db(mode):
    db = sqlite3.connect(MODES[mode]['db'])
    db.execute("PRAGMA query_only=ON")
    return db


def lookup_strategy(db, player, hand, history):
    hand_sorted = sorted(hand)
    hand_str = ','.join(str(d) for d in hand_sorted)
    if not history:
        history_str = "opening"
    else:
        history_str = ' -> '.join(history)
    key = f"P{player}|{hand_str}|{history_str}"
    row = db.execute("SELECT actions FROM strategy WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    actions = {}
    for part in row[0].split('|'):
        action, prob = part.split(':')
        actions[action] = float(prob)
    return actions


def sample_action(strategy):
    total = sum(strategy.values())
    r = random.random() * total
    cumulative = 0.0
    for action, prob in strategy.items():
        cumulative += prob
        if r <= cumulative:
            return action
    return list(strategy.keys())[-1]


def valid_bids_after(last_bid, all_bids):
    bid_index = {b: i for i, b in enumerate(all_bids)}
    if last_bid is None:
        return all_bids[:]
    idx = bid_index.get(last_bid, -1)
    return all_bids[idx + 1:]


def resolve_challenge(dice_p0, dice_p1, last_bid):
    parts = last_bid.split('x')
    bid_qty = int(parts[0])
    bid_face = int(parts[1])
    all_dice = dice_p0 + dice_p1
    count = sum(1 for d in all_dice if d == bid_face or d == 1)
    return count < bid_qty, count, bid_qty, bid_face


def get_game_state():
    if 'game' not in session:
        return None
    return json.loads(session['game'])


def save_game_state(state):
    session['game'] = json.dumps(state)


# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/play')
def play():
    mode = get_mode(request)
    return render_template('play.html', mode=mode, config=MODES[mode])


@app.route('/explorer')
def explorer():
    mode = get_mode(request)
    return render_template('explorer.html', mode=mode, config=MODES[mode])


# --- Game API ---

@app.route('/api/new_game', methods=['POST'])
def new_game():
    data = request.get_json() or {}
    mode = data.get('mode', '2v2')
    if mode not in MODES:
        mode = '2v2'
    cfg = MODES[mode]
    all_bids = get_all_bids(cfg['total_dice'])

    human_first = data.get('human_first', random.choice([True, False]))
    human_dice = sorted([random.randint(1, 6) for _ in range(cfg['dice_per_player'])])
    ai_dice = sorted([random.randint(1, 6) for _ in range(cfg['dice_per_player'])])

    human_player = 0 if human_first else 1
    ai_player = 1 - human_player

    state = {
        'mode': mode,
        'human_dice': human_dice,
        'ai_dice': ai_dice,
        'human_player': human_player,
        'ai_player': ai_player,
        'history': [],
        'current_turn': 0,
        'game_over': False,
        'result': None,
    }
    save_game_state(state)

    resp = {
        'human_dice': human_dice,
        'human_player': human_player,
        'human_goes_first': human_first,
        'history': [],
        'your_turn': human_player == 0,
        'valid_bids': all_bids[:],
        'can_call_liar': False,
    }

    if not human_first:
        return ai_move_and_respond(state)

    return jsonify(resp)


def ai_move_and_respond(state):
    mode = state['mode']
    cfg = MODES[mode]
    all_bids = get_all_bids(cfg['total_dice'])

    db = get_db(mode)
    strategy = lookup_strategy(db, state['ai_player'], state['ai_dice'], state['history'])
    db.close()

    if strategy:
        action = sample_action(strategy)
    else:
        last_bid = state['history'][-1] if state['history'] else None
        options = valid_bids_after(last_bid, all_bids)
        if not options or (last_bid and random.random() < 0.5):
            action = 'liar'
        else:
            action = random.choice(options)

    state['history'].append(action)
    state['current_turn'] = 1 - state['current_turn']

    if action == 'liar':
        last_bid = state['history'][-2]
        challenger_wins, actual_count, bid_qty, bid_face = resolve_challenge(
            state['human_dice'] if state['human_player'] == 0 else state['ai_dice'],
            state['ai_dice'] if state['human_player'] == 0 else state['human_dice'],
            last_bid
        )
        state['game_over'] = True
        human_wins = not challenger_wins
        state['result'] = {
            'challenger': 'ai',
            'last_bid': last_bid,
            'actual_count': actual_count,
            'bid_qty': bid_qty,
            'bid_face': bid_face,
            'human_wins': human_wins,
            'ai_dice': state['ai_dice'],
        }
        save_game_state(state)
        return jsonify({
            'human_dice': state['human_dice'],
            'human_player': state['human_player'],
            'history': state['history'],
            'your_turn': False,
            'game_over': True,
            'result': state['result'],
            'valid_bids': [],
            'can_call_liar': False,
        })

    last_bid = state['history'][-1] if state['history'] else None
    save_game_state(state)

    return jsonify({
        'human_dice': state['human_dice'],
        'human_player': state['human_player'],
        'history': state['history'],
        'your_turn': True,
        'game_over': False,
        'valid_bids': valid_bids_after(last_bid, all_bids),
        'can_call_liar': last_bid is not None,
    })


@app.route('/api/move', methods=['POST'])
def human_move():
    state = get_game_state()
    if not state or state['game_over']:
        return jsonify({'error': 'No active game'}), 400

    if state['current_turn'] != state['human_player']:
        return jsonify({'error': 'Not your turn'}), 400

    data = request.get_json()
    action = data.get('action')
    if not action:
        return jsonify({'error': 'No action provided'}), 400

    mode = state['mode']
    cfg = MODES[mode]
    all_bids = get_all_bids(cfg['total_dice'])

    last_bid = state['history'][-1] if state['history'] else None
    if action == 'liar':
        if last_bid is None:
            return jsonify({'error': 'Cannot call liar on opening'}), 400
    else:
        valid = valid_bids_after(last_bid, all_bids)
        if action not in valid:
            return jsonify({'error': f'Invalid bid: {action}'}), 400

    state['history'].append(action)
    state['current_turn'] = 1 - state['current_turn']

    if action == 'liar':
        challenged_bid = state['history'][-2]
        p0_dice = state['human_dice'] if state['human_player'] == 0 else state['ai_dice']
        p1_dice = state['ai_dice'] if state['human_player'] == 0 else state['human_dice']
        challenger_wins, actual_count, bid_qty, bid_face = resolve_challenge(
            p0_dice, p1_dice, challenged_bid
        )
        state['game_over'] = True
        state['result'] = {
            'challenger': 'human',
            'last_bid': challenged_bid,
            'actual_count': actual_count,
            'bid_qty': bid_qty,
            'bid_face': bid_face,
            'human_wins': challenger_wins,
            'ai_dice': state['ai_dice'],
        }
        save_game_state(state)
        return jsonify({
            'human_dice': state['human_dice'],
            'human_player': state['human_player'],
            'history': state['history'],
            'your_turn': False,
            'game_over': True,
            'result': state['result'],
            'valid_bids': [],
            'can_call_liar': False,
        })

    save_game_state(state)
    return ai_move_and_respond(state)


# --- Explorer API ---

@app.route('/api/explorer/node')
def explorer_node():
    mode = request.args.get('mode', '2v2')
    if mode not in MODES:
        mode = '2v2'
    cfg = MODES[mode]
    all_bids = get_all_bids(cfg['total_dice'])
    bid_index = {b: i for i, b in enumerate(all_bids)}
    all_hands = get_all_hands(cfg['dice_per_player'])

    history_param = request.args.get('history', '').strip()
    history = [h.strip() for h in history_param.split(',') if h.strip()] if history_param else []
    player = len(history) % 2

    if not history:
        history_str = "opening"
    else:
        history_str = ' -> '.join(history)

    db = get_db(mode)
    hands = []
    all_actions_set = set()

    for hand_str in all_hands:
        key = f"P{player}|{hand_str}|{history_str}"
        row = db.execute("SELECT actions FROM strategy WHERE key = ?", (key,)).fetchone()
        actions = {}
        if row:
            for part in row[0].split('|'):
                action, prob = part.split(':')
                actions[action] = float(prob)
                all_actions_set.add(action)
        hands.append({
            'hand': hand_str,
            'actions': actions,
        })

    db.close()

    def action_sort_key(a):
        if a == 'liar':
            return len(all_bids)
        return bid_index.get(a, -1)

    all_actions = sorted(all_actions_set, key=action_sort_key)
    navigable_actions = [a for a in all_actions if a != 'liar']

    return jsonify({
        'player': player,
        'history': history,
        'history_display': history_str,
        'hands': hands,
        'all_actions': all_actions,
        'navigable_actions': navigable_actions,
    })


if __name__ == '__main__':
    missing = []
    for mode, cfg in MODES.items():
        if not os.path.exists(cfg['db']):
            missing.append(cfg['db'])
    if missing:
        print(f"WARNING: Missing DB files: {', '.join(missing)}")
        print("Run 'python3 convert_strategy.py' first.")
    print("Starting Liar's Dice at http://localhost:5050")
    app.run(host='127.0.0.1', port=5050, debug=False)
