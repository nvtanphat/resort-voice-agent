"""Evidence-bound device acceptance record. Never convert NOT_RUN into a PASS.

The template intentionally contains no invented hardware scores. A signed-off
record can be stored off-device by hotel operations; customer data must not be
attached. This script does NOT perform hardware tests by itself.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CHECKS = (
    'approved_hotel_policies_four_languages',
    'signed_knowledge_update_and_rollback',
    'separate_https_staff_ingress_and_guest_denial',
    'staff_rbac_and_independent_verification',
    'real_langgraph_restart_and_interrupt_resume',
    'real_chromium_guest_and_staff_workflow',
    'offline_unplugged_end_to_end',
    'audio_vi_recorded_wer_cer_latency',
    'audio_en_recorded_wer_cer_latency',
    'audio_zh_recorded_wer_cer_latency',
    'audio_ko_recorded_wer_cer_latency',
    'tts_four_languages_loudspeaker',
    'joint_voice_rag_heldout_on_device',
    'thermal_memory_cpu_sustained_load',
    'power_loss_reboot_and_queue_recovery',
    'encrypted_off_device_backup_and_restore',
    'privacy_retention_and_staff_incident_drill',
    'physical_kiosk_tamper_and_network_segmentation',
    'secure_clock_and_effective_date_offline',
)


def template() -> dict:
    return {'product':'Concierge Kiosk', 'appliance_id':'REPLACE_DEVICE_ID',
        'reviewer':'REPLACE_REVIEWER', 'reviewed_at_utc':'',
        'checks':{name:{'status':'NOT_RUN','evidence':''} for name in CHECKS}}


def evaluate(data: dict) -> dict:
    if not data.get('appliance_id') or data['appliance_id'].startswith('REPLACE'):
        raise ValueError('Specific appliance ID is required')
    if not data.get('reviewer') or data['reviewer'].startswith('REPLACE'):
        raise ValueError('Named human reviewer is required')
    when = datetime.fromisoformat(data.get('reviewed_at_utc','').replace('Z','+00:00'))
    if when.tzinfo is None or when.utcoffset() != timezone.utc.utcoffset(when):
        raise ValueError('Use timezone-aware UTC review time')
    results = data.get('checks')
    if not isinstance(results, dict) or set(results) != set(CHECKS):
        raise ValueError('All defined checks must be reported, with no omissions')
    counts = {'PASS':0,'FAIL':0,'NOT_RUN':0}
    for name, item in results.items():
        if not isinstance(item, dict) or item.get('status') not in counts:
            raise ValueError('Invalid check status: '+name)
        if item['status'] == 'PASS' and len(str(item.get('evidence','')).strip()) < 12:
            raise ValueError('PASS requires concrete evidence reference: '+name)
        counts[item['status']] += 1
    return {'appliance_id':data['appliance_id'], 'reviewed_at_utc':data['reviewed_at_utc'],
            'check_count':len(CHECKS),'counts':counts,
            'ready_for_site_acceptance':counts['PASS']==len(CHECKS)}


def main() -> int:
    parser=argparse.ArgumentParser(description='Hardware acceptance with explicit evidence/no phantom passes')
    parser.add_argument('--template', action='store_true')
    parser.add_argument('--input', type=Path)
    args=parser.parse_args()
    if args.template:
        print(json.dumps(template(), indent=2))
        return 0
    if args.input is None:
        parser.error('--input required unless --template')
    report=evaluate(json.loads(args.input.read_text(encoding='utf-8')))
    print(json.dumps(report,indent=2))
    return 0 if report['ready_for_site_acceptance'] else 1


if __name__ == '__main__':
    sys.exit(main())
