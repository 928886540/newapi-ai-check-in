#!/usr/bin/env python3
"""
自动签到脚本
"""

import asyncio
import hashlib
import json
import sys
import os
from datetime import datetime
from dotenv import load_dotenv
from utils.config import AppConfig
from utils.notify import notify
from utils.balance_hash import load_balance_hash, save_balance_hash
from checkin import CheckIn

load_dotenv(override=True)

BALANCE_HASH_FILE = "balance_hash.txt"


def generate_balance_hash(balances: dict) -> str:
    """生成余额数据的hash"""
    simple_balances = {}
    if balances:
        for account_key, account_balances in balances.items():
            quota_list = []
            for _, balance_info in account_balances.items():
                quota_list.append(balance_info["quota"])
            simple_balances[account_key] = quota_list

    balance_json = json.dumps(simple_balances, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(balance_json.encode("utf-8")).hexdigest()[:16]


async def main():
    print("🚀 newapi.ai multi-account auto check-in script started (using Camoufox)")
    print(f'🕒 Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    app_config = AppConfig.load_from_env()
    print(f"⚙️ Loaded {len(app_config.providers)} provider(s)")

    if not app_config.accounts:
        print("❌ Unable to load account configuration, program exits")
        return 1

    print(f"⚙️ Found {len(app_config.accounts)} account(s)")

    last_balance_hash = load_balance_hash(BALANCE_HASH_FILE)

    success_count = 0
    total_count = 0
    notification_content = []
    current_balances = {}
    need_notify = False
    account_status_list = []
    account_balance_map = {}  # 存储每个账号的余额

    for i, account_config in enumerate(app_config.accounts):
        account_key = f"account_{i + 1}"
        account_name = account_config.get_display_name(i)
        if len(notification_content) > 0:
            notification_content.append("\n-------------------------------")

        try:
            provider_config = app_config.get_provider(account_config.provider)
            if not provider_config:
                print(f"❌ {account_name}: Provider '{account_config.provider}' configuration not found")
                need_notify = True
                notification_content.append(
                    f"[FAIL] {account_name}: Provider '{account_config.provider}' configuration not found"
                )
                account_status_list.append((account_name, "❌ 配置缺失"))
                continue

            print(f"🌀 Processing {account_name} using provider '{account_config.provider}'")
            checkin = CheckIn(account_name, account_config, provider_config, global_proxy=app_config.global_proxy)
            results = await checkin.execute()

            total_count += len(results)

            account_success = False
            successful_methods = []
            failed_methods = []
            already_checked_in = False

            this_account_balances = {}
            account_result = f"📣 {account_name} Summary:\n"
            for auth_method, success, user_info in results:
                status = "✅ SUCCESS" if success else "❌ FAILED"
                account_result += f"  {status} with {auth_method} authentication\n"

                if success and user_info and user_info.get("success"):
                    account_success = True
                    success_count += 1
                    successful_methods.append(auth_method)
                    account_result += f"    💰 {user_info['display']}\n"
                    current_quota = user_info["quota"]
                    current_used = user_info["used_quota"]
                    current_bonus = user_info["bonus_quota"]
                    this_account_balances[f"{auth_method}"] = {
                        "quota": current_quota,
                        "used": current_used,
                        "bonus": current_bonus,
                    }
                    if user_info.get("already_checked_in"):
                        already_checked_in = True
                else:
                    failed_methods.append(auth_method)
                    error_msg = user_info.get("error", "Unknown error") if user_info else "Unknown error"
                    account_result += f"    🔺 {str(error_msg)}\n"

            if account_success:
                current_balances[account_key] = this_account_balances
                # 存入余额映射
                for method, bal_info in this_account_balances.items():
                    account_balance_map[account_name] = bal_info

            if not account_success and results:
                need_notify = True
                print(f"🔔 {account_name} all authentication methods failed, will send notification")

            if failed_methods and successful_methods:
                need_notify = True
                print(f"🔔 {account_name} has some failed authentication methods, will send notification")

            success_count_methods = len(successful_methods)
            failed_count_methods = len(failed_methods)

            account_result += f"\n📊 Statistics: {success_count_methods}/{len(results)} methods successful"
            if failed_count_methods > 0:
                account_result += f" ({failed_count_methods} failed)"

            notification_content.append(account_result)

            if account_success:
                if already_checked_in:
                    account_status_list.append((account_name, "⏭️ 今日已签到，跳过"))
                else:
                    account_status_list.append((account_name, "✅ 签到成功"))
            else:
                account_status_list.append((account_name, "❌ 签到失败"))

        except Exception as e:
            print(f"❌ {account_name} processing exception: {e}")
            need_notify = True
            notification_content.append(f"❌ {account_name} Exception: {str(e)[:100]}...")
            account_status_list.append((account_name, f"❌ 异常: {str(e)[:50]}"))

    current_balance_hash = generate_balance_hash(current_balances) if current_balances else None
    print(f"\n\nℹ️ Current balance hash: {current_balance_hash}, Last balance hash: {last_balance_hash}")
    if current_balance_hash:
        if last_balance_hash is None:
            need_notify = True
            print("🔔 First run detected, will send notification with current balances")
        elif current_balance_hash != last_balance_hash:
            need_notify = True
            print("🔔 Balance changes detected, will send notification")
        else:
            print("ℹ️ No balance changes detected")

    if current_balance_hash:
        save_balance_hash(BALANCE_HASH_FILE, current_balance_hash)

    if need_notify and notification_content:
        summary = [
            "-------------------------------",
            "📢 Check-in result statistics:",
            f"🔵 Success: {success_count}/{total_count}",
            f"🔴 Failed: {total_count - success_count}/{total_count}",
        ]

        if success_count == total_count:
            summary.append("✅ All accounts check-in successful!")
        elif success_count > 0:
            summary.append("⚠️ Some accounts check-in successful")
        else:
            summary.append("❌ All accounts check-in failed")

        time_info = f'🕓 Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'

        notify_content = "\n\n".join([time_info, "\n".join(notification_content), "\n".join(summary)])

        print(notify_content)
        notify.push_message("Check-in Alert", notify_content, msg_type="text")
        print("🔔 Notification sent due to failures or balance changes")
    else:
        print("ℹ️ All accounts successful and no balance changes detected, notification skipped")

    # ========== 生成汇总并传递给 GitHub Actions ==========
    if account_status_list:
        # 统计各状态数量
        total = len(account_status_list)
        success_cnt = sum(1 for _, s in account_status_list if "✅" in s and "跳过" not in s)
        skip_cnt = sum(1 for _, s in account_status_list if "⏭️" in s)
        fail_cnt = sum(1 for _, s in account_status_list if "❌" in s)
        
        # 构建汇总内容（带余额）
        summary_lines = []
        summary_lines.append("")
        summary_lines.append("=" * 70)
        summary_lines.append("📋 签到结果速览")
        summary_lines.append("-" * 70)
        
        # 显示每个账号的状态 + 余额
        for name, status in account_status_list:
            # 查找该账号的余额
            balance_info = account_balance_map.get(name, {})
            balance_val = balance_info.get("quota", 0)
            balance_str = f"💰 {balance_val:.2f}" if balance_val > 0 else ""
            summary_lines.append(f"  {name:<28}  →  {status:<12}  {balance_str}")
        
        summary_lines.append("-" * 70)
        summary_lines.append(f"📊 总计: {total} 个账号  |  ✅ 成功: {success_cnt}  |  ⏭️ 跳过: {skip_cnt}  |  ❌ 失败: {fail_cnt}")
        summary_lines.append("=" * 70)
        
        # 打印到控制台
        for line in summary_lines:
            print(line)
        
        # 写入 GitHub Actions 输出
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"summary<<EOF\n")
                f.write("\n".join(summary_lines))
                f.write("\nEOF\n")
    # ====================================================

    sys.exit(0 if success_count > 0 else 1)


def run_main():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️ Program interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error occurred during program execution: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_main()
