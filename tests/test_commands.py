from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from concierge_kiosk.agent.understanding.commands import (
    Command, CommandSlot, command_schema, commands_from_turn_plan,
    parse_commands, validate_commands,
)
from concierge_kiosk.agent.understanding.turn_plan import TurnIntent, TurnPlan


def test_command_schema_is_closed_and_turn_plan_maps_to_commands():
    query = 'Please bring 2 towels to room 305'
    plan = TurnPlan((TurnIntent(
        'write', 'amenity_delivery',
        (CommandSlot('quantity', '2'), CommandSlot('room_number', '305')),
        None, None),))
    commands = commands_from_turn_plan(
        plan, query=query, enabled_request_kinds=frozenset({'facilities'}))
    assert commands is not None
    assert commands[0].type == 'StartGoal'
    assert commands[0].goal == 'amenity_delivery'
    assert command_schema()['additionalProperties'] is False


def test_model_command_cannot_invent_a_slot_value():
    query = 'Please bring 2 towels to room 305'
    raw = json.dumps({'commands': [{
        'type': 'StartGoal', 'goal': 'amenity_delivery',
        'slots': [{'name': 'quantity', 'text': '2'}, {'name': 'room_number', 'text': '999'}],
    }]})
    assert parse_commands(raw, query=query,
                          enabled_request_kinds=frozenset({'facilities'})) is None


def test_commands_keep_confirmation_and_cancel_non_authoritative():
    assert validate_commands(
        [Command('Confirm', confirmed=True)], query='yes', pending_reply='confirm')
    assert validate_commands([Command('Cancel')], query='cancel')
    assert validate_commands([Command('Confirm', confirmed=True)], query='yes',
                             pending_reply='room_number') is None
