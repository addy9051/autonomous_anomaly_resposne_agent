#!/usr/bin/env python3
"""
Slack Connectivity Test Script.

Used to verify that the BOT_TOKEN and Channel IDs are correctly configured
in the .env file before running the main agent system.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
import asyncio
from shared.config import get_settings
from shared.utils import setup_logging

async def test_slack():
    setup_logging()
    settings = get_settings()
    
    print("\n--- Slack Configuration Check ---")
    print(f"Token (first 10 chars): {settings.integrations.slack_bot_token[:10]}...")
    print(f"Alert Channel ID: {settings.integrations.slack_alert_channel}")
    
    if not settings.integrations.slack_bot_token:
        print("❌ Error: SLACK_BOT_TOKEN is missing in .env")
        return

    client = AsyncWebClient(token=settings.integrations.slack_bot_token)
    
    try:
        # 1. Test Auth
        print("📡 Testing authentication...")
        auth_test = await client.auth_test()
        print(f"✅ Authenticated as: {auth_test['user']} (ID: {auth_test['user_id']})")
        print(f"   Team: {auth_test['team']} (ID: {auth_test['team_id']})")
        
        # 2. Test Connection to Alert Channel
        print(f"📡 Testing access to channel {settings.integrations.slack_alert_channel}...")
        try:
            # Check if we can get info about the channel
            conv_info = await client.conversations_info(channel=settings.integrations.slack_alert_channel)
            channel_name = conv_info['channel']['name']
            
            # Check if bot is the owner or member
            is_member = conv_info['channel'].get('is_member', False)
            if not is_member:
                print(f"⚠️ Warning: Channel '{channel_name}' found, but bot is NOT a member.")
                print(f"   ACTION REQUIRED: Invite the bot to the channel first.")
                print(f"   Type `/invite @{auth_test['user']}` in channel #{channel_name}")
            else:
                print(f"✅ Successfully found channel: #{channel_name} (Member: Yes)")

        except SlackApiError as e:
            if e.response["error"] == "channel_not_found":
                print(f"❌ Error: Channel {settings.integrations.slack_alert_channel} not found.")
                print("   Note: Ensure the Bot user has been invited to this channel.")
                print(f"   HINT: Type `/invite @{auth_test['user']}` in the channel.")
            else:
                print(f"❌ Slack API Error: {e.response['error']}")
            return

        # 3. Send Test Message
        print("📡 Sending test message...")
        response = await client.chat_postMessage(
            channel=settings.integrations.slack_alert_channel,
            text=f"🚀 *Anomaly Agent Integration Test*\nTime: `{asyncio.get_event_loop().time()}`\nStatus: Online"
        )
        print(f"✅ Message sent! TS: {response['ts']}")
        print("\n🎉 Slack integration is working correctly!")

    except SlackApiError as e:
        print(f"❌ Slack API error: {e.response['error']}")
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_slack())
