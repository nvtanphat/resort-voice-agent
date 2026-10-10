"""Deployment-stable command prompt and catalog serialization, without model I/O."""
from __future__ import annotations

import json
from typing import Any, Mapping

from concierge_kiosk.agent.tools.service_slots import SERVER_EXTRACTED_SLOTS
from concierge_kiosk.core.domain_profile import preference_policy, rag_policy, supported_languages
from concierge_kiosk.domain.service_registry import accepted_slots
from .command_types import CHITCHAT_KINDS


_CATALOG_MARKER = '\nAVAILABLE_SERVICES='

_COMMAND_INSTRUCTIONS = (
    'Interpret one hotel guest turn into compact JSON commands. Cover EVERY independently '
    'requested action, including a shorter request beside a longer one. One item list is ONE '
    'StartGoal; different aspects of one factual question are ONE AskInfo. '
    'Select services by their action criteria in AVAILABLE_SERVICES, never department or default. '
    'LIKELY_SERVICES is advisory. Examples demonstrate both single and composed intentions. '
    'StartGoal requests execution; CheckAvailability asks about free capacity without booking; '
    'AskInfo asks facts, price, hours or policy; Navigate asks directions; Plan asks advice. '
    'Past, completed, quoted, reported, hypothetical or negated service mentions grant no execution '
    'intent. Keep their governing words in text; never turn an inner verb into a new request. '
    'AskStatus checks an earlier request. Cancel/Modify require an open draft or earlier request; '
    'declining an item in a fresh order does not cancel a ticket. Confirm approves a pending task. '
    'SetSlot/CorrectSlot answer PENDING_REPLY. SetPreference needs a stated lasting preference and '
    'verbatim evidence. Handoff requests staff or reports a complaint. SwitchLanguage changes language. '
    'ChitChat is social; Clarify is insufficient intent. '
    'text is the COMPLETE original predicate for that command, including its complements and scope. '
    'All text, item, value and evidence must be verbatim guest words. Emit NO slots array: the server '
    'extracts room, quantities, units, time, date and venue. Optional item is only the exact object '
    'phrase for an object-taking service. Never invent permission, booking, price or completion. '
    'conditional=true only for an explicit availability condition. refers_to_context=true only '
    'when the guest points back to CONTEXT.last_verified_topic without naming it. '
    'OPEN_DRAFT is unconfirmed server state. Every action remains a proposal requiring guest consent. '
)


def _command_catalog(enabled_request_kinds: frozenset[str]) -> list[dict[str, Any]]:
    """Every enabled registry service, in registry order (the authority for goals and slots)."""
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
    # Only the slots a model has to point at; the server reads counts, clock
    # times, dates and party sizes from the guest turn itself.
    return [{'service_mode': code,
             'slots': [name for name in accepted_slots(code) if name not in SERVER_EXTRACTED_SLOTS],
             'description': definition.description}
            for code, definition in SERVICE_DEFINITIONS.items()
            if definition.request_kind in enabled_request_kinds
            and definition.request_kind != 'directions']


def command_output_spec() -> str:
    """The JSON shape of every command, stated in the prompt (vocabularies from config)."""
    preferences = '|'.join(sorted(preference_policy().fields))
    return (
        'Answer with one line of JSON: {"commands":[...]}. Command shapes: '
        '{"type":"StartGoal","goal":SERVICE_MODE,"text":GUEST_PREDICATE} '
        '(optional "item":GUEST_OBJECT; optional "conditional":true); '
        '{"type":"CheckAvailability","goal":SERVICE_MODE,"text":GUEST_PREDICATE}; '
        '{"type":"AskInfo","query":GUEST_WORDS} with optional "facet":'
        + '|'.join(sorted(rag_policy().facet_fact_types)) + '; '
        '{"type":"Navigate","query":GUEST_WORDS}; {"type":"Plan","query":GUEST_WORDS}; '
        '{"type":"AskStatus"}; {"type":"Cancel"}; {"type":"Modify"}; {"type":"Clarify"}; '
        '{"type":"Confirm","confirmed":true}; '
        '{"type":"SetSlot","field":ACCEPTED_SLOT,"value":GUEST_WORDS} (or CorrectSlot); '
        '{"type":"SetPreference","field":' + preferences + ',"value":VALUE,"evidence":GUEST_WORDS}; '
        '{"type":"SwitchLanguage","target":' + '|'.join(sorted(supported_languages())) + '}; '
        '{"type":"Handoff","reason":TEXT}; '
        '{"type":"ChitChat","kind":' + '|'.join(CHITCHAT_KINDS) + '}. '
        'When CONTEXT is present, StartGoal, AskInfo and Navigate also carry "refers_to_context":true|false.'
    )


def command_system_message(enabled_request_kinds: frozenset[str]) -> dict[str, str]:
    """The static part of every command prompt: instructions plus the enabled catalog.

    It depends only on the deployment, so it is byte-identical on every turn and
    comes first: the local runtime then reuses its KV cache instead of prefilling
    the catalog again on CPU, and startup can warm exactly this prefix. Everything
    that changes per turn goes in the user message, with the guest turn last.
    """
    return {'role': 'system', 'content': _COMMAND_INSTRUCTIONS + command_output_spec() + _CATALOG_MARKER
            + json.dumps(_command_catalog(enabled_request_kinds), ensure_ascii=False, separators=(',', ':'))}


def prompt_catalog(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The service catalog a command payload offered the model (for diagnostics)."""
    system = payload['messages'][0]['content']
    return json.loads(system.rsplit(_CATALOG_MARKER, 1)[1])
