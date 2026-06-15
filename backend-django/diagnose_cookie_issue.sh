#!/bin/bash
# 诊断 Cookie 重复问题

echo "=================================="
echo "抖音账号 Cookie 诊断工具"
echo "=================================="
echo

docker exec zq-backend python manage.py shell << 'PYEOF'
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.storage import load_storage_state
from core.douyin.runtime.credential import session_fingerprint_from_state

accounts = DouyinAccount.objects.filter(is_deleted=False).order_by('sys_create_datetime')

print('=' * 80)
print(f'数据库中共有 {accounts.count()} 个账号')
print('=' * 80)

# 收集所有信息
session_map = {}
uid_tt_map = {}

for i, acc in enumerate(accounts, 1):
    print(f'\n【账号 {i}】')
    print(f'  ID: {acc.id}')
    print(f'  昵称: {acc.nickname}')
    print(f'  sec_uid: {acc.sec_uid or "(无)"}')
    print(f'  状态: {acc.status} - {acc.get_status_display()}')
    print(f'  凭证状态: {acc.credential_state}')
    print(f'  自动回复: {"✓ 开启" if acc.auto_reply_enabled else "✗ 关闭"}')
    print(f'  最后心跳: {acc.last_heartbeat or "(无)"}')

    # 读取 cookie
    state = load_storage_state(str(acc.id))
    if state:
        sessionid, uid_tt = session_fingerprint_from_state(state)
        sid_short = sessionid[:20] if sessionid else "(无)"
        uid_short = uid_tt[:20] if uid_tt else "(无)"

        print(f'  sessionid: {sid_short}...')
        print(f'  uid_tt: {uid_short}...')

        # 收集到 map 中
        if sessionid:
            if sessionid not in session_map:
                session_map[sessionid] = []
            session_map[sessionid].append({
                'id': str(acc.id)[:8],
                'nickname': acc.nickname,
                'sec_uid': acc.sec_uid or '(无)'
            })

        if uid_tt:
            if uid_tt not in uid_tt_map:
                uid_tt_map[uid_tt] = []
            uid_tt_map[uid_tt].append({
                'id': str(acc.id)[:8],
                'nickname': acc.nickname,
                'sec_uid': acc.sec_uid or '(无)'
            })

        # 检查 cookie 内容
        cookies = {c.get("name"): c.get("value", "") for c in state.get("cookies", [])}
        print(f'  Cookie 数量: {len(cookies)}')
        print(f'  包含 sessionid: {"✓" if "sessionid" in cookies else "✗"}')
        print(f'  包含 sessionid_ss: {"✓" if "sessionid_ss" in cookies else "✗"}')
        print(f'  包含 uid_tt: {"✓" if "uid_tt" in cookies else "✗"}')

        # 检查发送凭证
        bd = state.get("_bd_ticket", {})
        has_send = bd.get('private_key') and bd.get('ticket') and bd.get('ts_sign')
        print(f'  发送凭证: {"✓ 完整" if has_send else "✗ 不完整"}')
    else:
        print(f'  ⚠️  登录态文件不存在或解密失败')

print('\n' + '=' * 80)
print('【重复检测】')
print('=' * 80)

# 检查 sessionid 重复
print('\n1. sessionid 重复检查:')
found_dup = False
for sid, acc_list in session_map.items():
    if len(acc_list) > 1:
        found_dup = True
        print(f'\n  ⚠️  发现重复！sessionid: {sid[:30]}...')
        print(f'  以下 {len(acc_list)} 个账号槽位使用了相同的 Cookie:')
        for acc_info in acc_list:
            print(f'    - {acc_info["nickname"]} (ID: {acc_info["id"]}..., sec_uid: {acc_info["sec_uid"]})')
        print(f'\n  💡 解决方案:')
        print(f'    1. 在浏览器中登录不同的抖音账号')
        print(f'    2. 删除其中一个账号槽位（推荐删除「用户7139479680080」）')
        print(f'    3. 重新导入不同抖音账号的 Cookie')

if not found_dup:
    print('  ✓ 没有发现 sessionid 重复')

# 检查 uid_tt 重复
print('\n2. uid_tt 重复检查:')
found_uid_dup = False
for uid, acc_list in uid_tt_map.items():
    if len(acc_list) > 1:
        found_uid_dup = True
        print(f'\n  ⚠️  发现重复！uid_tt: {uid[:30]}...')
        print(f'  以下账号使用了相同的 uid_tt:')
        for acc_info in acc_list:
            print(f'    - {acc_info["nickname"]} (ID: {acc_info["id"]}...)')

if not found_uid_dup:
    print('  ✓ 没有发现 uid_tt 重复')

print('\n' + '=' * 80)
print('【诊断结论】')
print('=' * 80)

if found_dup or found_uid_dup:
    print('\n❌ 发现问题：多个账号槽位使用了同一个抖音账号的 Cookie')
    print('\n这解释了为什么「用户7139479680080」无法触发自动回复：')
    print('  - 它实际上和「哈啊」是同一个抖音账号')
    print('  - Worker 会自动去重，只托管其中优先级高的一个')
    print('  - 向「用户7139479680080」发消息，实际上是向「哈啊」发消息')
else:
    print('\n✓ Cookie 隔离正常，没有发现重复')

PYEOF

echo
echo "=================================="
echo "检查完成"
echo "=================================="
