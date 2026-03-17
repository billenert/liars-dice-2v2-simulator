#!/usr/bin/env python3
"""Liar's Dice 2v2 — play against MCCFR bot locally."""

import sqlite3
import random
import json
import os
from flask import Flask, render_template, jsonify, request, session

app = Flask(__name__)
app.secret_key = os.urandom(24)

DB_FILE = "strategy_2v2.db"

# All valid bids in order (4 total dice, faces 2-6, aces wild)
ALL_BIDS = []
for qty in range(1, 5):  # 1 to 4
    for face in range(2, 7):  # 2 to 6
        ALL_BIDS.append(f"{qty}x{face}")
BID_INDEX = {b: i for i, b in enumerate(ALL_BIDS)}


def get_db():
    """Get a thread-local DB connection."""
    db = sqlite3.connect(DB_FILE)
    db.execute("PRAGMA query_only=ON")
    return db


def lookup_strategy(db, player, hand, history):
    """Look up the MCCFR strategy for a given info set."""
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
    """Sample an action from a strategy distribution."""
    r = random.random()
    cumulative = 0.0
    for action, prob in strategy.items():
        cumulative += prob
        if r <= cumulative:
            return action
    return list(strategy.keys())[-1]


def valid_bids_after(last_bid):
    """Return all bids strictly higher than last_bid."""
    if last_bid is None:
        return ALL_BIDS[:]
    idx = BID_INDEX.get(last_bid, -1)
    return ALL_BIDS[idx + 1:]


def resolve_challenge(dice_p0, dice_p1, last_bid):
    """Resolve a 'liar' call. Returns (challenger_wins, actual_count, bid_qty, bid_face)."""
    parts = last_bid.split('x')
    bid_qty = int(parts[0])
    bid_face = int(parts[1])
    all_dice = dice_p0 + dice_p1
    # Count matching dice (including aces/ones as wild)
    count = sum(1 for d in all_dice if d == bid_face or d == 1)
    return count < bid_qty, count, bid_qty, bid_face


def get_game_state():
    """Get or create game state from session."""
    if 'game' not in session:
        return None
    return json.loads(session['game'])


def save_game_state(state):
    session['game'] = json.dumps(state)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/new_game', methods=['POST'])
def new_game():
    data = request.get_json() or {}
    human_first = data.get('human_first', random.choice([True, False]))

    human_dice = sorted([random.randint(1, 6) for _ in range(2)])
    ai_dice = sorted([random.randint(1, 6) for _ in range(2)])

    # human_player: which P# the human is (0 = goes first, 1 = goes second)
    human_player = 0 if human_first else 1
    ai_player = 1 - human_player

    state = {
        'human_dice': human_dice,
        'ai_dice': ai_dice,
        'human_player': human_player,
        'ai_player': ai_player,
        'history': [],
        'current_turn': 0,  # P0 always starts
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
        'valid_bids': ALL_BIDS[:],
        'can_call_liar': False,
    }

    # If AI goes first, make AI's move
    if not human_first:
        return ai_move_and_respond(state)

    return jsonify(resp)


def ai_move_and_respond(state):
    """Have the AI make its move and return response."""
    db = get_db()
    strategy = lookup_strategy(db, state['ai_player'], state['ai_dice'], state['history'])
    db.close()

    if strategy:
        action = sample_action(strategy)
    else:
        # Fallback: if no strategy found, pick a random valid bid or call liar
        last_bid = state['history'][-1] if state['history'] else None
        options = valid_bids_after(last_bid)
        if not options or (last_bid and random.random() < 0.5):
            action = 'liar'
        else:
            action = random.choice(options)

    state['history'].append(action)
    state['current_turn'] = 1 - state['current_turn']

    if action == 'liar':
        # AI called liar on human's bid
        last_bid = state['history'][-2]
        challenger_wins, actual_count, bid_qty, bid_face = resolve_challenge(
            state['human_dice'] if state['human_player'] == 0 else state['ai_dice'],
            state['ai_dice'] if state['human_player'] == 0 else state['human_dice'],
            last_bid
        )
        # Challenger is AI
        state['game_over'] = True
        human_wins = not challenger_wins  # if AI's challenge succeeds, human loses
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

    # AI made a bid, now it's human's turn
    last_bid = state['history'][-1] if state['history'] else None
    save_game_state(state)

    return jsonify({
        'human_dice': state['human_dice'],
        'human_player': state['human_player'],
        'history': state['history'],
        'your_turn': True,
        'game_over': False,
        'valid_bids': valid_bids_after(last_bid),
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

    # Validate action
    last_bid = state['history'][-1] if state['history'] else None
    if action == 'liar':
        if last_bid is None:
            return jsonify({'error': 'Cannot call liar on opening'}), 400
    else:
        valid = valid_bids_after(last_bid)
        if action not in valid:
            return jsonify({'error': f'Invalid bid: {action}'}), 400

    state['history'].append(action)
    state['current_turn'] = 1 - state['current_turn']

    if action == 'liar':
        # Human called liar on AI's bid
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

    # Human made a bid, now AI responds
    save_game_state(state)
    return ai_move_and_respond(state)


if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        print(f"ERROR: {DB_FILE} not found. Run 'python3 convert_strategy.py' first.")
        exit(1)
    print("Starting Liar's Dice at http://localhost:5050")
    app.run(host='127.0.0.1', port=5050, debug=False)
