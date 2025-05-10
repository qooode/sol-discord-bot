import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import time
import asyncio
import json
from dotenv import load_dotenv
from utils.message_tracker import MessageTracker
from utils.ai_handler import AIHandler
from utils.channel_manager import ChannelManager
from utils.decision_engine import DecisionEngine
from config import BOT_CONFIG
from datetime import datetime, timedelta
import typing
import requests
import re

# Load environment variables
load_dotenv()

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.members = True
intents.guilds = True

# Use a hidden command prefix nobody would use accidentally
bot = commands.Bot(command_prefix='$$!sol!$$', intents=intents, help_command=None)

# Initialize utilities
message_tracker = MessageTracker(
    context_window=BOT_CONFIG['context_window'],
    max_age_hours=BOT_CONFIG['context_max_age']
)
ai_handler = AIHandler(
    api_key=os.getenv('OPENROUTER_API_KEY'),
    model=BOT_CONFIG['ai_model'],
    system_prompt=BOT_CONFIG['system_prompt']
)
channel_manager = ChannelManager()
decision_engine = DecisionEngine(api_key=os.getenv('OPENROUTER_API_KEY'))  # Sol's brain for autonomous decisions

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Connected to {len(bot.guilds)} servers')
    
    # Set normal human-like status
    activities = [
        discord.Activity(type=discord.ActivityType.playing, name="valorant"),
        discord.Activity(type=discord.ActivityType.listening, name="spotify"),
        discord.Activity(type=discord.ActivityType.watching, name="youtube"),
        None  # Sometimes no status
    ]
    
    # Set random activity or no activity
    await bot.change_presence(activity=random.choice(activities))
    
    # Sync slash commands
    try:
        print("Syncing slash commands...")
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
        
    # Process commands first
    await bot.process_commands(message)
    
    # Check for moderator command with ".sol" prefix (more explicit than just "sol")
    if message.content.lower().startswith(".sol "):
        await process_natural_command(message)
        return

    # Check if bot is active in this channel
    if not channel_manager.is_channel_active(message.channel.id):
        return
    
    # ==== EMOJI REACTION SYSTEM ====
    # Randomly decide whether to react to the message with a server emoji
    if message.guild:  # Only in servers, not DMs
        # Higher chance to react when directly mentioned
        is_mentioned = bot.user.mentioned_in(message)
        reaction_chance = 0.5 if is_mentioned else 0.15  # 50% for mentions, 15% otherwise
        
        # First check the random chance BEFORE making any API calls to save tokens
        if random.random() < reaction_chance:
            try:
                # Get all custom emojis from the server
                custom_emojis = message.guild.emojis
                if custom_emojis:
                    # If we have very few emojis, just pick one randomly without AI
                    if len(custom_emojis) <= 5:
                        matching_emoji = random.choice(custom_emojis)
                        await message.add_reaction(matching_emoji)
                        print(f"Reacted to message with random emoji: {matching_emoji.name}")
                    else:
                        # Use AI to select an appropriate emoji
                        emoji_prompt = f"""
                        Based on this message, select ONE appropriate custom emoji to react with.
                        
                        MESSAGE: "{message.content}"
                        
                        AVAILABLE EMOJIS:
                        {', '.join([f":{emoji.name}:" for emoji in custom_emojis])}
                        
                        Return ONLY the name of ONE emoji that best matches the message sentiment or content. 
                        Just return the name without : symbols or any explanation. Only one word.
                        """
                        
                        # Use a simpler API call to get a quick response
                        payload = {
                            "model": "google/gemini-2.5-flash-preview",
                            "messages": [{
                                "role": "user", 
                                "content": emoji_prompt
                            }],
                            "max_tokens": 10  # Very short response needed
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                            "Content-Type": "application/json"
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=2  # Short timeout
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if "choices" in data and len(data["choices"]) > 0:
                                emoji_name = data["choices"][0]["message"]["content"].strip()
                                
                                # Find the emoji by name
                                matching_emoji = discord.utils.get(custom_emojis, name=emoji_name)
                                
                                # If no exact match, try partial match
                                if not matching_emoji:
                                    for emoji in custom_emojis:
                                        if emoji_name.lower() in emoji.name.lower():
                                            matching_emoji = emoji
                                            break
                                
                                # If still no match, pick random emoji
                                if not matching_emoji and custom_emojis:
                                    matching_emoji = random.choice(custom_emojis)
                                
                                # React with the selected emoji
                                if matching_emoji:
                                    await message.add_reaction(matching_emoji)
                                    print(f"Reacted to message with emoji: {matching_emoji.name}")
            except Exception as e:
                print(f"Error adding reaction: {e}")
    
    # ==== MODERATION CHECK ====
    # Check if the message violates community rules
    violates_rules, rule_violated, explanation, alternative_suggestion = decision_engine.check_moderation(
        message_content=message.content,
        user_id=message.author.id,
        channel_id=message.channel.id,
        member=message.author
    )
    
    # If the message violates rules, warn the user
    if violates_rules:
        # Count recent violations to determine response
        violation_count = decision_engine.get_violation_count(message.author.id, hours=24)
        
        # Don't warn for every violation - add randomness to avoid spam
        should_warn = True
        if violation_count > 2:
            # After 2 warnings, only warn 50% of the time to reduce spam
            should_warn = random.random() < 0.5
        
        if should_warn:
            # Generate shorter warning message based on violation count
            if violation_count == 0:
                # First violation - very casual reminder
                warning = f"hey {message.author.mention} quick heads up: {rule_violated} (warning 1/3)"
                if alternative_suggestion:
                    warning += f"\n(Consider instead: {alternative_suggestion})"
            elif violation_count == 1:
                # Second violation - still casual
                warning = f"{message.author.mention} {rule_violated} (warning 2/3, next violation = stricter warning)"
                if alternative_suggestion:
                    warning += f"\n(Legal alternative: {alternative_suggestion})"
            elif violation_count == 2:
                # Third violation - more direct but still brief
                warning = f"{message.author.mention} {rule_violated}. {explanation} (warning 3/3, next violation = timeout risk)"
                if alternative_suggestion:
                    warning += f"\n(Suggested alternative: {alternative_suggestion})"
            else:
                # Multiple violations - more direct but still brief
                timeout_mins = BOT_CONFIG['moderation'].get('timeout_minutes', 1)
                warning = f"{message.author.mention} {rule_violated}. {explanation} (violation #{violation_count+1}, timeout risk: {timeout_mins}min)"
                if alternative_suggestion:
                    warning += f"\n(Try this instead: {alternative_suggestion})"
            
            # Send warning as a reply to the offending message
            try:
                # Get message content for logging
                message_content = message.content
                
                # Try to delete the violating message if configured
                delete_violations = BOT_CONFIG['moderation'].get('delete_violations', True)
                deleted = False
                
                if delete_violations:
                    try:
                        await message.delete()
                        deleted = True
                        print(f"MODERATION: Deleted message from {message.author.name} for rule violation")
                    except Exception as e:
                        print(f"Failed to delete message: {e}")
                
                # Send the warning message
                warning_msg = await message.channel.send(warning) if deleted else await message.reply(warning)
                print(f"MODERATION: Warning sent to {message.author.name} for rule violation: {rule_violated}")
                
                # Set up auto-deletion of warning message if configured
                warning_delete_seconds = BOT_CONFIG['moderation'].get('warning_delete_seconds', 0)
                if warning_delete_seconds > 0:
                    # Schedule deletion of our warning
                    try:
                        await asyncio.sleep(warning_delete_seconds)
                        await warning_msg.delete()
                        print(f"MODERATION: Auto-deleted warning message after {warning_delete_seconds} seconds")
                    except Exception as e:
                        print(f"Error auto-deleting warning message: {e}")
                
                # Log the violation to the configured log channel if set
                log_channel_id = BOT_CONFIG['moderation'].get('log_channel_id')
                if log_channel_id:
                    try:
                        log_channel = bot.get_channel(int(log_channel_id))
                        if log_channel:
                            # Format timestamp
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            
                            # Create log embed
                            embed = discord.Embed(
                                title="Moderation Action",
                                color=discord.Color.orange(),
                                timestamp=datetime.now()
                            )
                            embed.add_field(name="User", value=f"{message.author.mention} ({message.author.name})", inline=False)
                            embed.add_field(name="Channel", value=f"{message.channel.mention}", inline=False)
                            embed.add_field(name="Rule Violated", value=rule_violated, inline=False)
                            embed.add_field(name="Action Taken", value=f"{'Message Deleted, ' if deleted else ''}Warning Issued", inline=False)
                            embed.add_field(name="Violation Count", value=f"{violation_count+1} in past 24h", inline=False)
                            embed.add_field(name="Message Content", value=message_content[:1000] if len(message_content) <= 1000 else f"{message_content[:997]}...", inline=False)
                            
                            if alternative_suggestion:
                                embed.add_field(name="Suggested Alternative", value=alternative_suggestion, inline=False)
                            
                            await log_channel.send(embed=embed)
                            print(f"MODERATION: Logged moderation action to channel #{log_channel.name}")
                    except Exception as e:
                        print(f"Error logging moderation action: {e}")
                
                # Store this warning in a temporary memory to avoid responding to this user's next few messages
                # This prevents Sol from responding casually to someone they just warned
                if not hasattr(decision_engine, 'recent_warnings'):
                    decision_engine.recent_warnings = {}
                
                # Mark this user as recently warned - with timestamp and duration based on severity
                warning_duration = 300  # Default: 5 minutes
                if violation_count > 2:
                    warning_duration = 1800  # 30 minutes for severe warnings
                elif violation_count > 0:
                    warning_duration = 600  # 10 minutes for repeat warnings
                
                decision_engine.recent_warnings[message.author.id] = {
                    'timestamp': time.time(),
                    'duration': warning_duration,
                    'rule': rule_violated
                }
                
                # Check if we should timeout the user based on settings and violation count
                warning_threshold = BOT_CONFIG['moderation'].get('warning_threshold', 3)
                auto_timeout = BOT_CONFIG['moderation'].get('auto_timeout', False)
                timeout_minutes = BOT_CONFIG['moderation'].get('timeout_minutes', 1)
                
                # If auto-timeout is enabled and user has exceeded threshold
                if auto_timeout and violation_count >= warning_threshold:
                    # Calculate timeout duration (increases with more violations)
                    # Base duration × (1 + excess violations)
                    excess_violations = violation_count - warning_threshold
                    timeout_duration = timeout_minutes * (1 + min(excess_violations, 5))  # Cap at 6x base duration
                    
                    try:
                        # Get the member object for timeout
                        if hasattr(message, 'guild') and message.guild:
                            member = message.guild.get_member(message.author.id)
                            if member:
                                # Calculate timeout end time
                                timeout_seconds = timeout_duration * 60
                                timeout_until = datetime.now() + timedelta(seconds=timeout_seconds)
                                
                                # Apply timeout
                                await member.timeout(timeout_until, reason=f"Automated timeout after {violation_count} rule violations in 24h")
                                
                                # Notify the user with shorter message
                                timeout_message = await message.channel.send(f"{message.author.mention} timed out for {timeout_duration}m due to repeated violations")
                                
                                # Log timeout to log channel if configured
                                if log_channel_id:
                                    try:
                                        log_channel = bot.get_channel(int(log_channel_id))
                                        if log_channel:
                                            timeout_embed = discord.Embed(
                                                title="Timeout Applied",
                                                color=discord.Color.red(),
                                                timestamp=datetime.now()
                                            )
                                            timeout_embed.add_field(name="User", value=f"{message.author.mention} ({message.author.name})", inline=False)
                                            timeout_embed.add_field(name="Duration", value=f"{timeout_duration} minutes", inline=False)
                                            timeout_embed.add_field(name="Reason", value=f"Automated timeout after {violation_count} rule violations in 24h", inline=False)
                                            
                                            await log_channel.send(embed=timeout_embed)
                                    except Exception as e:
                                        print(f"Error logging timeout: {e}")
                                
                                # Auto-delete timeout message if configured
                                if warning_delete_seconds > 0:
                                    try:
                                        await asyncio.sleep(warning_delete_seconds)
                                        await timeout_message.delete()
                                    except Exception as e:
                                        print(f"Error auto-deleting timeout message: {e}")
                                
                                print(f"MODERATION: Applied {timeout_duration}m timeout to {message.author.name} for repeated violations")
                    except Exception as e:
                        print(f"Error applying timeout: {e}")
                
                # Skip further processing of this message
                return
            except Exception as e:
                print(f"Error sending moderation warning: {e}")
    
    # Continue with normal message processing...
    
    # Check if the bot is mentioned or in a DM channel or its name is in the message
    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)
    name_mentioned = BOT_CONFIG['name'].lower() in message.content.lower()
    
    # Additional check for direct mention via user ID - critical for proper response handling
    user_id_mention = f"<@{bot.user.id}>" in message.content
    if user_id_mention:
        print(f"DIRECT USER ID MENTION DETECTED: <@{bot.user.id}>")
        is_mentioned = True  # Ensure we count ID mentions as direct mentions
    
    # Additional check for role mentions that might include the bot
    has_role_mention = False
    if message.role_mentions:
        bot_member = message.guild.get_member(bot.user.id) if message.guild else None
        if bot_member:
            # Check if any of the mentioned roles are roles that the bot has
            for role in message.role_mentions:
                if role in bot_member.roles:
                    print(f"BOT ROLE MENTION DETECTED: {role.name}")
                    is_mentioned = True
                    has_role_mention = True
                    break
    
    # Special case for when the message starts with "Sol" or "@Sol"
    if message.content.lower().startswith("sol ") or message.content.lower().startswith("@sol "):
        print("BOT NAME PREFIX DETECTED")
        is_mentioned = True
        name_mentioned = True
    
    # Add message to tracker regardless - Sol should always remember messages
    is_burst = message_tracker.add_message(message.author.id, message.content, message.channel.id)
    
    # Check if this message is a reply to another message
    replying_to_bot = False
    reply_author_name = None
    
    if message.reference and message.reference.message_id:
        try:
            # Properly fetch the message being replied to (using async)
            replied_message = await message.channel.fetch_message(message.reference.message_id)
            
            # Check if the reply is to the bot
            if replied_message.author.id == bot.user.id:
                replying_to_bot = True
                print(f"Message is replying to the bot")
                # Force mention flag to ensure we respond to replies to us
                is_mentioned = True
            else:
                reply_author_name = replied_message.author.display_name
                print(f"Message is replying to {reply_author_name}, not the bot")
        except Exception as e:
            print(f"Error fetching replied message: {e}")
    
    # =====  AVOID RESPONDING TO RECENTLY WARNED USERS =====
    # Check if this user was recently warned and we should avoid casual conversation
    recently_warned = False
    if hasattr(decision_engine, 'recent_warnings') and message.author.id in decision_engine.recent_warnings:
        warning_data = decision_engine.recent_warnings[message.author.id]
        warning_time = warning_data['timestamp']
        warning_duration = warning_data['duration']
        
        # Check if warning is still active
        if time.time() - warning_time < warning_duration:
            recently_warned = True
            # Only respond if directly mentioned (user explicitly wants a response)
            if not (is_mentioned or is_dm or replying_to_bot):
                print(f"Avoiding casual response to recently warned user: {message.author.name}")
                return  # Skip responding unless directly addressed
    
    # Get conversation context for this user in this channel
    # For complex questions or uncertain topics, use extended context (up to 100 messages)
    needs_extended_context = any(term in message.content.lower() for term in [
        "how", "why", "explain", "what is", "what are", "can you", "could you", "help me", "help with", 
        "problem", "issue", "error", "doesn't work", "not working", "broken", "tutorial", "guide",
        "setup", "configure", "settings"
    ])
    
    context = message_tracker.get_context(message.author.id, message.channel.id, extended=needs_extended_context)
    
    # Debug output for all messages
    print(f"\n--- RECEIVED MESSAGE: '{message.content[:50]}...' FROM {message.author.name} ---")
    
    # ===== SOL'S AUTONOMOUS DECISION PROCESS =====
    
    # 1. Wait for complete thoughts if needed - for any message
    should_wait = message_tracker.should_wait_for_more_context(message.author.id, message.channel.id)
    additional_wait = decision_engine.should_wait_longer(message.content, is_burst)
    
    # If Sol should wait and wasn't directly mentioned, don't respond yet
    if (should_wait or additional_wait) and not is_mentioned:
        print("Waiting for more messages before deciding...")
        return
    
    # 2. THE KEY CHANGE: Always ask the AI decision engine about EVERY message
    should_respond, reason = decision_engine.should_respond(
        message_content=message.content,
        message=message,
        user_id=message.author.id,
        channel_id=message.channel.id,
        reply_to_message_id=message.reference.message_id if message.reference else None
    )
    
    # 3. Immediate yes cases - override the decision engine
    # If directly mentioned or in DM or replying to bot, always respond regardless of decision engine
    force_respond = is_mentioned or is_dm or replying_to_bot
    
    # Only proceed if decision engine says yes or we're forcing a response
    if not (should_respond or force_respond):  
        print(f"NOT responding to message - {reason}")
        return
            
    # 4. Sol has decided to respond - determine response style
    if force_respond:
        if replying_to_bot:
            print(f"RESPONDING TO MESSAGE! (Forced response: message is a reply to Sol)")
        else:
            print(f"RESPONDING TO MESSAGE! (Forced response: direct mention or DM)")
    else:
        print(f"RESPONDING TO MESSAGE! Reason: {reason}")
    response_type = decision_engine.determine_response_type(message.content, context)
    response_length = decision_engine.decide_response_length(response_type, message.content)
    
    # 5. Wait if part of a burst to see if user sends more messages
    if is_burst and not is_mentioned:
        wait_time = random.uniform(2.0, 4.0)
        print(f"Message is part of burst, waiting {wait_time:.1f}s for more context...")
        await asyncio.sleep(wait_time)
        
        # Check again if we should wait even longer
        if message_tracker.should_wait_for_more_context(message.author.id, message.channel.id):
            print("User still typing, waiting more...")
            return
            
        # Get updated context after waiting
        context = message_tracker.get_context(message.author.id, message.channel.id)
    
    # 6. Human-like typing delay based on message complexity
    typing_time = random.uniform(BOT_CONFIG['typing_delay_min'], BOT_CONFIG['typing_delay_max'])
    
    # Adjust typing time based on expected response length
    if response_length == "short":
        typing_time *= 0.7  # Faster for short responses
    elif response_length == "medium":
        typing_time *= 1.2  # Normal for medium responses
    else:  # "long"
        typing_time *= 1.8  # Slower for long responses
    
    print(f"Typing for {typing_time:.1f}s with {response_length} {response_type} response style")
    
    # 7. Generate and send response
    async with message.channel.typing():
        # Artificial delay to make responses feel more natural
        await asyncio.sleep(typing_time)
        
        # Add hints about response type to help model generate appropriately
        enhanced_context = context.copy()
        if len(enhanced_context) > 0 and enhanced_context[-1]['role'] == 'user':
            # Add hint to the user's last message (invisible to the user)
            hint = f"\n\n[Respond with a {response_length} {response_type} response]"
            enhanced_context[-1]['content'] += hint
        
        # SIMPLE FIX: Check if message is asking about what someone else said
        message_lower = message.content.lower()
        user_reference_patterns = [
            r'what did (\w+) say',
            r'what (\w+) said',
            r'opinion on what (\w+) said',
            r'about what (\w+) said'
        ]
        
        referenced_user = None
        for pattern in user_reference_patterns:
            matches = re.findall(pattern, message_lower)
            if matches:
                referenced_user = matches[0]
                break
        
        # If we found a referenced user, try to fetch more message history
        if referenced_user:
            try:
                print(f"Detected reference to user '{referenced_user}', fetching more context...")
                # Fetch the last 10 messages from this channel
                async for msg in message.channel.history(limit=10):
                    # Skip messages from the bot itself
                    if msg.author == bot.user:
                        continue
                    
                    # If we find a message from the referenced user, add it to context
                    if referenced_user.lower() in msg.author.name.lower() or referenced_user.lower() in msg.author.display_name.lower():
                        print(f"Found message from referenced user: {msg.content}")
                        # Add this message to the beginning of our context
                        enhanced_context.insert(0, {
                            "role": "user",
                            "content": f"[Previous message from {msg.author.display_name}]: {msg.content}"
                        })
                        break
            except Exception as e:
                print(f"Error fetching message history: {e}")
        
        # Use AI to decide if this message needs internet search (especially for follow-ups)
        needs_internet = decision_engine.needs_internet_search(message.content, context)
        if needs_internet:
            print(f"AI determined this message needs internet search - likely factual or follow-up question")
        
        # Get response from AI
        start_time = time.time()
        response = ai_handler.get_response(enhanced_context, needs_internet)
        end_time = time.time()
        
        # Calculate response time
        ai_time = end_time - start_time
        print(f"Got AI response in {ai_time:.2f}s")
        
        # Only respond if AI generated a meaningful response
        if response and response.strip():
            # Record bot's response in the tracker
            message_tracker.add_bot_response(message.author.id, response, message.channel.id)
            
            # Check if we should use reply feature
            should_use_reply = False
            
            # First, check if the message is a reply to someone else's message
            if message.reference and message.reference.message_id:
                # If the user is replying to a message, we should reply to maintain the thread context
                should_use_reply = True
                print(f"Message is a reply to another message, using reply feature to maintain thread context")
            elif replying_to_bot:
                # If message is a reply to the bot, always use reply feature
                should_use_reply = True
                print(f"Message is a reply to Sol, using reply feature to maintain thread context")
            else:
                # Get recent messages in the channel
                recent_msgs = message_tracker.get_recent_channel_messages(message.channel.id, 10)
                
                # If there are messages between the current message and the last time Sol responded
                # (someone else added messages after the user messaged Sol), use reply mode
                if len(recent_msgs) >= 3:
                    # Look for patterns where: user message -> other messages -> sol's response
                    for i in range(len(recent_msgs) - 2):
                        # Check if messages have the user IDs we expect
                        if 'user_id' not in recent_msgs[i] or 'user_id' not in recent_msgs[i+1]:
                            continue
                            
                        # If this sequence has: user message -> different user message -> (current sol response)
                        if (recent_msgs[i]['user_id'] == str(message.author.id) and 
                            recent_msgs[i+1]['user_id'] != str(message.author.id) and
                            recent_msgs[i+1]['user_id'] != str(bot.user.id)):
                            should_use_reply = True
                            break
            
            # Send response - with or without reply
            if should_use_reply:
                # Use reply feature to clarify who Sol is talking to
                await message.reply(response)
            else:
                # Normal response when the conversation flow is clear
                await message.channel.send(response)
        else:
            print("AI generated empty response, not sending anything")

# Slash Commands
# Check if user is admin or moderator
def is_admin(interaction: discord.Interaction):
    # In DMs, consider the user an admin
    if interaction.guild is None:
        return True
        
    # Server owner is always admin
    if interaction.user.id == interaction.guild.owner_id:
        return True
    
    # Check for admin/moderator permissions in the guild
    member = interaction.guild.get_member(interaction.user.id)
    if member:
        # Check for admin permission
        if member.guild_permissions.administrator:
            return True
            
        # Check for manage server permission
        if member.guild_permissions.manage_guild:
            return True
            
        # Check for moderator permission
        if member.guild_permissions.moderate_members or member.guild_permissions.manage_messages:
            return True
            
        # Check for specific admin/mod roles by name
        admin_role_names = [
            'Admin', 'Mod', 'Moderator', 'staff', 'Staff', 'administrator', 'owner', 'Owner',
            'App Dev',  # Your custom roles
            # Add any other role names that should have admin access to Sol
        ]
        
        # Check if member has any of the admin role names
        for role in member.roles:
            if role.name in admin_role_names or role.name.lower() in [name.lower() for name in admin_role_names]:
                return True
                
    # Not an admin/mod
    return False

@bot.tree.command(name="help", description="Admin commands for Sol")
async def slash_help(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    help_text = f"**sol admin commands**\n\n"
    
    help_text += f"**Channel Control:**\n"
    help_text += f"`/activate` - turn sol on in current channel\n"
    help_text += f"`/deactivate` - turn sol off in current channel\n"
    help_text += f"`/activateall` - turn sol on everywhere\n"
    help_text += f"`/deactivateall` - turn sol off everywhere\n"
    help_text += f"`/channels` - list active channels\n\n"
    
    help_text += f"**Settings:**\n"
    help_text += f"`/personality` - set sol's personality traits:\n"
    help_text += f"  • `chatty` - how often sol joins conversations (1-10)\n"
    help_text += f"  • `patience` - how long sol waits for complete thoughts (1-10)\n"
    help_text += f"  • `formality` - how formal sol's responses are (1-10)\n"
    help_text += f"`/setwindow [size]` - set how many messages sol remembers\n"
    help_text += f"`/setcasual [level]` - set how casual sol's replies are (1-10)\n"
    help_text += f"`/timing [min] [max]` - set typing delay in seconds\n\n"
    
    help_text += f"**Permissions:**\n"
    help_text += f"`/setcommandaccess [role] [add/remove]` - set roles that can use .sol commands\n"
    help_text += f"`/listcommandaccess` - list roles that can use .sol commands\n"
    help_text += f"`/exemptrolefrommod [role] [add/remove]` - set roles exempt from moderation\n"
    help_text += f"`/listexemptroles` - list roles exempt from moderation\n\n"
    
    help_text += f"**Management:**\n"
    help_text += f"`/status` - check all settings\n"
    help_text += f"`/clear` - clear memory\n"
    help_text += f"`/reload` - reload system prompt\n"
    help_text += f"`/spark [channel] [topic]` - make sol start a conversation in a quiet channel\n\n"
    
    help_text += f"**Natural Language Commands:**\n"
    help_text += f"Use `.sol` prefix for natural language commands, examples:\n"
    help_text += f"`.sol warn [user] for [reason]` - warn a user\n"
    help_text += f"`.sol mute [user] for [time] because [reason]` - timeout a user\n"
    help_text += f"`.sol say in #channel [message]` - send a message to a channel\n"
    help_text += f"`.sol search [query] and tell [user]` - search for information\n"
    help_text += f"`.sol open this as an issue on github: [issue]` - create a GitHub issue\n"
    help_text += f"`.sol create github issue in [org/repo]: [title]` - create an issue in a specific repo"
    
    await interaction.response.send_message(help_text, ephemeral=True)

@bot.tree.command(name="activate", description="Activate Sol in a specific channel")
async def slash_activate(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Always use current channel for simplicity
    channel = interaction.channel
    
    if channel_manager.is_channel_active(channel.id):
        await interaction.response.send_message(f"already active in {channel.mention}", ephemeral=True)
        return
        
    channel_manager.activate_channel(channel.id)
    await interaction.response.send_message(f"sol activated in {channel.mention}", ephemeral=True)

@bot.tree.command(name="deactivate", description="Deactivate Sol in the current channel")
async def slash_deactivate(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Always use current channel for simplicity
    channel = interaction.channel
    
    if not channel_manager.is_channel_active(channel.id):
        await interaction.response.send_message(f"already inactive in {channel.mention}", ephemeral=True)
        return
        
    channel_manager.deactivate_channel(channel.id)
    await interaction.response.send_message(f"sol deactivated in {channel.mention}", ephemeral=True)

@bot.tree.command(name="activateall", description="Activate Sol in all channels")
async def slash_activate_all(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    if channel_manager.set_global_active(True):
        # Clear any channel restrictions
        channel_manager.channel_mode = False
        channel_manager.active_channels.clear()
        
        responses = [
            "i'm back everywhere", 
            "hey everyone", 
            "listening in all channels now", 
            "i'm on globally",
            "ready to chat anywhere 👋"
        ]
        await interaction.response.send_message(random.choice(responses), ephemeral=True)
    else:
        await interaction.response.send_message("already active everywhere", ephemeral=True)

@bot.tree.command(name="deactivateall", description="Deactivate Sol in all channels")
async def slash_deactivate_all(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    if channel_manager.set_global_active(False):
        await interaction.response.send_message("ok, going quiet everywhere. use /activateall when you need me", ephemeral=True)
    else:
        await interaction.response.send_message("already off everywhere", ephemeral=True)

@bot.command()
async def ping(ctx):
    """Check if the bot is responding"""
    responses = ["yep?", "here", "sup", "yo", "hmm?", "👋"]
    await ctx.send(random.choice(responses))
    
@bot.tree.command(name="status", description="Check Sol's status and settings")
async def slash_status(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    status = channel_manager.get_status()
    
    status_text = f"**sol status**\n\n"
    
    status_text += f"**Activity:**\n"
    status_text += f"globally active: {status['global_active']}\n"
    status_text += f"channel-only mode: {status['channel_mode']}\n"
    status_text += f"active in {status['channel_count']} channels\n"
    status_text += f"active in this channel: {channel_manager.is_channel_active(interaction.channel.id)}\n\n"
    
    status_text += f"**Memory & Responses:**\n"
    status_text += f"context window: {BOT_CONFIG['context_window']} messages\n"
    status_text += f"context max age: {BOT_CONFIG['context_max_age']} hours\n"
    status_text += f"ambient reply chance: {BOT_CONFIG['ambient_reply_chance']}\n"
    status_text += f"casualness level: {BOT_CONFIG['casualness']}/10\n"
    status_text += f"typing delay: {BOT_CONFIG['typing_delay_min']}-{BOT_CONFIG['typing_delay_max']}s\n\n"
    
    status_text += f"**Permissions:**\n"
    command_roles = BOT_CONFIG.get('command_roles', [])
    if command_roles:
        role_names = []
        for role_id in command_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                role_names.append(role.name)
        status_text += f"command roles: {', '.join(role_names)}\n"
    else:
        status_text += f"command roles: none (only admins can use commands)\n"
    
    exempt_roles = BOT_CONFIG.get('moderation', {}).get('exempt_roles', [])
    if exempt_roles:
        exempt_role_names = []
        for role_id in exempt_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                exempt_role_names.append(role.name)
        status_text += f"moderation exempt roles: {', '.join(exempt_role_names)}\n\n"
    else:
        status_text += f"moderation exempt roles: none\n\n"
    
    status_text += f"**Technical:**\n"
    status_text += f"name: {BOT_CONFIG['name']}\n"
    status_text += f"model: {BOT_CONFIG['ai_model']}\n"
    status_text += f"command prefix: .sol\n\n"
    
    # Add GitHub integration status
    status_text += f"**GitHub Integration:**\n"
    github_token = os.getenv('GITHUB_TOKEN')
    github_repo = os.getenv('GITHUB_REPO')
    status_text += f"github token: {'configured' if github_token else 'not configured'}\n"
    status_text += f"default repo: {github_repo if github_repo else 'not configured'}"
    
    await interaction.response.send_message(status_text, ephemeral=True)

@bot.tree.command(name="clear", description="Clear conversation history")
async def slash_clear(interaction: discord.Interaction):
    # Admin only now
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    message_tracker.clear_history(interaction.user.id, interaction.channel.id)
    await interaction.response.send_message("memory cleared", ephemeral=True)

# Add a reload command for system prompt
@bot.tree.command(name="channels", description="List channels where Sol is active")
async def list_channels(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    status = channel_manager.get_status()
    
    if not status['global_active']:
        await interaction.response.send_message("sol is currently off everywhere", ephemeral=True)
        return
    
    if not status['channel_mode']:
        await interaction.response.send_message("sol is active in all channels", ephemeral=True)
        return
    
    if len(status['active_channels']) == 0:
        await interaction.response.send_message("sol is not active in any channels", ephemeral=True)
        return
    
    channels_text = "**sol is active in:**\n"
    for channel_id in status['active_channels']:
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            channels_text += f"- {channel.mention}\n"
    
    await interaction.response.send_message(channels_text, ephemeral=True)

# Settings commands
@bot.tree.command(name="setwindow", description="Set how many messages Sol remembers")
@app_commands.describe(size="Number of messages to remember (5-50)")
async def set_context_window(interaction: discord.Interaction, size: int):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Validate input
    if size < 5 or size > 50:
        await interaction.response.send_message("please use a value between 5-50 messages", ephemeral=True)
        return
        
    # Update config
    BOT_CONFIG['context_window'] = size
    
    # Update message tracker
    global message_tracker
    message_tracker = MessageTracker(BOT_CONFIG['context_window'])
    
    await interaction.response.send_message(f"sol will now remember {size} messages", ephemeral=True)

@bot.tree.command(name="setcasual", description="Set how casual Sol's responses are")
@app_commands.describe(level="Casualness level (1-10, 1=formal, 10=super casual)")
async def set_casualness(interaction: discord.Interaction, level: int):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Validate input
    if level < 1 or level > 10:
        await interaction.response.send_message("please use a value between 1-10", ephemeral=True)
        return
        
    # Update config
    BOT_CONFIG['casualness'] = level
    
    # Update system prompt based on casualness
    old_prompt = BOT_CONFIG['system_prompt']
    
    # We'll need to update the AI handler with the new system prompt
    global ai_handler
    ai_handler = AIHandler(
        api_key=os.getenv('OPENROUTER_API_KEY'),
        model=BOT_CONFIG['ai_model'],
        system_prompt=BOT_CONFIG['system_prompt']
    )
    
    await interaction.response.send_message(f"sol's casualness level set to {level}/10", ephemeral=True)

@bot.tree.command(name="personality", description="Set Sol's conversation style and AI behavior")
@app_commands.describe(
    chatty="How chatty Sol should be (1-10, 1=responds rarely, 10=joins every conversation)",
    patience="How patient Sol should be (1-10, 1=responds immediately, 10=waits for complete thoughts)",
    formality="How formal Sol should be (1-10, 1=very casual, 10=extremely formal)"
)
async def set_personality(interaction: discord.Interaction, chatty: int = None, patience: int = None, formality: int = None):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Validate inputs
    changes_made = []
    
    if chatty is not None:
        if chatty < 1 or chatty > 10:
            await interaction.response.send_message("chatty level must be between 1-10", ephemeral=True)
            return
        # Convert 1-10 scale to 0.0-1.0 probability for the decision engine
        chatty_value = chatty / 10.0
        BOT_CONFIG['ai_personality'] = BOT_CONFIG.get('ai_personality', {})
        BOT_CONFIG['ai_personality']['chatty'] = chatty_value
        changes_made.append(f"chattiness: {chatty}/10")
    
    if patience is not None:
        if patience < 1 or patience > 10:
            await interaction.response.send_message("patience level must be between 1-10", ephemeral=True)
            return
        BOT_CONFIG['ai_personality'] = BOT_CONFIG.get('ai_personality', {})
        BOT_CONFIG['ai_personality']['patience'] = patience
        # Also update the patience level in the message tracker
        message_tracker.patience_level = patience
        changes_made.append(f"patience: {patience}/10")
    
    if formality is not None:
        if formality < 1 or formality > 10:
            await interaction.response.send_message("formality level must be between 1-10", ephemeral=True)
            return
        BOT_CONFIG['ai_personality'] = BOT_CONFIG.get('ai_personality', {})
        BOT_CONFIG['ai_personality']['formality'] = formality
        changes_made.append(f"formality: {formality}/10")
    
    if not changes_made:
        # Display current settings if no changes made
        personality = BOT_CONFIG.get('ai_personality', {})
        chatty_val = int(personality.get('chatty', 0.5) * 10) if 'chatty' in personality else 5
        patience_val = personality.get('patience', 5)
        formality_val = personality.get('formality', 5)
        
        settings = f"Current personality settings:\n"
        settings += f"• Chattiness: {chatty_val}/10\n"
        settings += f"• Patience: {patience_val}/10\n"
        settings += f"• Formality: {formality_val}/10"
        
        await interaction.response.send_message(settings, ephemeral=True)
        return
    
    # Let user know what changed
    changes_text = ", ".join(changes_made)
    await interaction.response.send_message(f"updated sol's personality: {changes_text}", ephemeral=True)


# Old setreply command has been removed in favor of /personality

@bot.tree.command(name="timing", description="Set typing delay in seconds")
@app_commands.describe(
    min_time="Minimum typing delay in seconds",
    max_time="Maximum typing delay in seconds"
)
async def set_timing(interaction: discord.Interaction, min_time: float, max_time: float):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Validate input
    if min_time < 0.2 or min_time > 10.0 or max_time < min_time or max_time > 20.0:
        await interaction.response.send_message("please use values between 0.2-10.0 for min and min-20.0 for max", ephemeral=True)
        return
        
    # Update config
    BOT_CONFIG['typing_delay_min'] = min_time
    BOT_CONFIG['typing_delay_max'] = max_time
    
    await interaction.response.send_message(f"sol's typing delay set to {min_time}-{max_time}s", ephemeral=True)
    
@bot.tree.command(name="reload", description="Reload Sol's system prompt")
async def reload_prompt(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # Reinitialize AI handler with system prompt
    global ai_handler
    ai_handler = AIHandler(
        api_key=os.getenv('OPENROUTER_API_KEY'),
        model=BOT_CONFIG['ai_model'],
        system_prompt=BOT_CONFIG['system_prompt']
    )
    
    await interaction.response.send_message("reloaded system prompt", ephemeral=True)

@bot.tree.command(name="spark", description="Make Sol continue or start a conversation based on channel history")
@app_commands.describe(
    channel="The channel to continue a conversation in (defaults to current channel)",
    topic="Optional topic suggestion (leave empty for Sol to analyze channel history)"
)
async def spark_conversation(interaction: discord.Interaction, 
                            channel: discord.TextChannel = None, 
                            topic: str = None):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
        
    # If no channel specified, use current channel
    if channel is None:
        channel = interaction.channel
    
    # Get 100 recent messages from the channel to analyze
    recent_msgs = message_tracker.get_recent_channel_messages(channel.id, 100)
    
    # Filter out bot commands and non-relevant messages
    filtered_msgs = []
    for msg in recent_msgs:
        content = msg.get('content', '')
        
        # Skip messages that look like bot commands
        if content.startswith('/') or content.startswith('$'):
            continue
            
        # Skip very short messages that don't add context
        if len(content.split()) <= 2:
            continue
            
        # Skip messages that are clearly directed at the bot as commands
        if content.lower().startswith(f"{BOT_CONFIG['name'].lower()} ") and len(content.split()) <= 3:
            continue
            
        # Add to filtered list
        filtered_msgs.append(msg)
    
    # Use filtered messages for analysis
    recent_msgs = filtered_msgs
    
    # Acknowledge command
    await interaction.response.send_message(f"analyzing conversations in {channel.mention}", ephemeral=True)
    
    # Prepare conversation history for AI
    context = []
    
    # Extract and track conversation patterns, including reply chains
    reply_chains = {}
    conversation_flow = []
    active_threads = {}
    
    for idx, msg in enumerate(recent_msgs):
        username = msg.get('username', 'Unknown')
        content = msg.get('content', '')
        user_id = str(msg.get('user_id', ''))
        
        # Check if this message is a reply to another message
        replied_to = msg.get('replied_to', None)
        
        if replied_to:
            # Record who replied to whom
            replied_to_user = replied_to.get('username', 'Unknown')
            if replied_to_user not in reply_chains:
                reply_chains[replied_to_user] = []
            reply_chains[replied_to_user].append(username)
            
            # Track conversation threads
            thread_id = replied_to.get('id', '') or replied_to.get('message_id', '')
            if thread_id:
                if thread_id not in active_threads:
                    active_threads[thread_id] = []
                active_threads[thread_id].append({'username': username, 'content': content})
        
        # Track overall flow
        conversation_flow.append({'username': username, 'content': content[:50], 'replied_to': replied_to})
    
    # Convert messages to format for AI analysis
    for msg in recent_msgs:
        role = "assistant" if str(msg.get('user_id', '')) == str(bot.user.id) else "user"
        name = msg.get('username', 'User') if role == "user" else None
        
        # Add reply context if present
        content = msg.get('content', '')
        replied_to = msg.get('replied_to', None)
        if replied_to:
            replied_to_user = replied_to.get('username', 'Unknown')
            content = f"[Replying to {replied_to_user}] {content}"
        
        context.append({
            "role": role,
            "content": content,
            "name": name
        })
    
    # Prepare reply chain information for the AI
    reply_chains_text = ""
    for replied_to_user, repliers in reply_chains.items():
        reply_chains_text += f"- {replied_to_user} was replied to by: {', '.join(repliers)}\n"
    
    # Prepare active threads information for the AI
    active_threads_text = ""
    for thread_id, messages in active_threads.items():
        if len(messages) > 0:
            participants = [msg['username'] for msg in messages]
            active_threads_text += f"- Conversation between {', '.join(set(participants))}\n"
    
    # First, use AI to perform a sophisticated analysis of the conversation history
    analysis_prompt = f"""
    You're performing a detailed analysis of a Discord conversation to identify the most engaging continuation opportunities.
    
    ANALYSIS APPROACH:
    First, analyze the conversation for:
    1. RECURRING THEMES - identify topics that appear regularly in this Discord channel
    2. Topic clusters - identify related messages that form coherent discussion threads
    3. Channel culture - what topics and conversation styles are typical in this community
    4. Open loops - questions, requests, opinions that haven't received adequate responses
    5. Engagement patterns - which specific topics generated the most interaction
    6. Participant interests - map individual users to their areas of interest/expertise
    7. Sentiment patterns - topics with emotional investment or enthusiasm
    8. Reply chains - who's talking directly to whom
    9. Conversation threads - ongoing back-and-forth exchanges between specific people
    10. Community focus - what specialized topics this community appears to be built around
    
    REPLY CHAIN DATA (who replies to whom):
    {reply_chains_text if reply_chains_text else "No clear reply chains detected."}
    
    ACTIVE CONVERSATION THREADS:
    {active_threads_text if active_threads_text else "No active threads detected."}
    
    STEP-BY-STEP REASONING:
    1. First, identify the most active individual conversations between specific users
    2. For each user, note what topics they're discussing and with whom
    3. Identify any replies that didn't get responses or questions that weren't answered
    4. Note which users and topics generate the most back-and-forth exchanges
    5. Determine which conversation thread would benefit most from continuation
    6. Identify how to join an existing conversation naturally rather than starting a new one
    
    Format your response as detailed JSON with these fields:
    - "recurring_themes": [array of topics that appear REGULARLY in the channel's history]
    - "channel_focus": overall description of what this community typically discusses
    - "topic_clusters": [array of distinct topics with descriptions]
    - "open_questions": [array of specific unanswered questions from the conversation]
    - "user_interests": {{mapping of usernames to their apparent interests/expertise}}
    - "engagement_hotspots": [specific moments or topics that generated strong engagement]
    - "continuation_opportunities": [ranked list of specific continuations possible that stay within the channel's typical themes]
    - "optimal_approach": detailed strategy for the most promising conversation continuation
    - "specific_references": [quotes or paraphrases you could reference from the conversation]
    - "key_participants": [usernames of most engaged participants to potentially mention]
    - "relevance_check": explanation of how your suggested continuation relates to recurring channel themes
    
    JSON RESPONSE ONLY:
    """
    
    # Add the analysis prompt to context
    analysis_context = context.copy()
    analysis_context.append({
        "role": "user",
        "content": analysis_prompt
    })
    
    # Get AI analysis of the conversation
    conversation_analysis = ai_handler.get_response(analysis_context)
    
    # Try to parse the JSON response
    try:
        # Extract JSON from the response
        json_start = conversation_analysis.find('{')
        json_end = conversation_analysis.rfind('}')
        
        if json_start != -1 and json_end != -1:
            json_str = conversation_analysis[json_start:json_end+1]
            analysis_data = json.loads(json_str)
            
            # Extract the sophisticated analysis results
            # Safely extract values and handle potential type issues
            topic_clusters = analysis_data.get('topic_clusters', [])
            open_questions = analysis_data.get('open_questions', [])
            user_interests = analysis_data.get('user_interests', {})
            engagement_hotspots = analysis_data.get('engagement_hotspots', [])
            continuation_opportunities = analysis_data.get('continuation_opportunities', [])
            optimal_approach = analysis_data.get('optimal_approach', '')
            specific_references = analysis_data.get('specific_references', [])
            key_participants = analysis_data.get('key_participants', [])
            
            # If any of these are strings instead of lists/dicts (sometimes AI formats incorrectly),
            # handle that defensively
            if isinstance(topic_clusters, str):
                topic_clusters = [topic_clusters]
            if isinstance(open_questions, str):
                open_questions = [open_questions]
            if isinstance(engagement_hotspots, str):
                engagement_hotspots = [engagement_hotspots]
            if isinstance(continuation_opportunities, str):
                continuation_opportunities = [continuation_opportunities]
            if isinstance(specific_references, str):
                specific_references = [specific_references]
            if isinstance(key_participants, str):
                key_participants = [key_participants]
        else:
            # Fallback if no JSON found
            topic_clusters = []
            open_questions = []
            user_interests = {}
            engagement_hotspots = []
            continuation_opportunities = []
            optimal_approach = ""
            specific_references = []
            key_participants = []
    except Exception as e:
        print(f"Error parsing conversation analysis: {e}")
        # Set fallback values
        topic_clusters = []
        open_questions = []
        user_interests = {}
        engagement_hotspots = []
        continuation_opportunities = []
        optimal_approach = ""
        specific_references = []
        key_participants = []
    
    # Handle different data types that might come back from the AI
    # Extract string values from potential dictionaries or other complex objects
    def extract_text(item):
        if isinstance(item, str):
            return item
        elif isinstance(item, dict) and 'description' in item:
            return item['description']
        elif isinstance(item, dict) and 'topic' in item:
            return item['topic']
        elif isinstance(item, dict) and 'text' in item:
            return item['text']
        elif isinstance(item, dict) and len(item) > 0:
            # Just take the first value if we can't find standard keys
            return str(list(item.values())[0])
        else:
            # Last resort - convert whatever it is to a string
            return str(item)
    
    # Convert potentially complex structures to simple strings
    topic_clusters_text = [extract_text(topic) for topic in topic_clusters] if topic_clusters else []
    open_questions_text = [extract_text(q) for q in open_questions] if open_questions else []
    engagement_hotspots_text = [extract_text(spot) for spot in engagement_hotspots] if engagement_hotspots else []
    continuation_opportunities_text = [extract_text(opp) for opp in continuation_opportunities] if continuation_opportunities else []
    specific_references_text = [extract_text(ref) for ref in specific_references] if specific_references else []
    
    # Format for the prompt
    # Convert user interests dictionary to a simple text representation
    user_interests_formatted = ""
    if user_interests:
        for user, interests in user_interests.items():
            # Handle if interests is a complex object
            interest_text = extract_text(interests) if not isinstance(interests, str) else interests
            user_interests_formatted += f"- {user}: {interest_text}\n"
    else:
        user_interests_formatted = "None specifically identified"
    
    # Format continuation opportunities
    continuation_formatted = "\n".join([f"- {opp}" for opp in continuation_opportunities_text[:3]]) if continuation_opportunities_text else "Use your judgment based on the conversation"
    
    # Format specific references
    references_formatted = "\n".join([f"- {ref}" for ref in specific_references_text[:3]]) if specific_references_text else "None specifically identified"
    
    # Extract additional theme data from analysis
    recurring_themes = []
    channel_focus = "Discord community"
    relevance_check = ""
    
    try:
        # Extract recurring themes if available
        if 'recurring_themes' in analysis_data and analysis_data['recurring_themes']:
            recurring_themes = [extract_text(theme) for theme in analysis_data['recurring_themes']]
            
        # Extract channel focus if available
        if 'channel_focus' in analysis_data and analysis_data['channel_focus']:
            channel_focus = extract_text(analysis_data['channel_focus'])
            
        # Extract relevance check if available
        if 'relevance_check' in analysis_data and analysis_data['relevance_check']:
            relevance_check = extract_text(analysis_data['relevance_check'])
    except Exception as e:
        print(f"Error extracting theme data: {e}")
    
    # Format recurring themes for the prompt
    recurring_themes_text = ", ".join(recurring_themes[:5]) if recurring_themes else "None specifically identified"
    
    # Now create a second prompt to generate the actual conversation starter
    starter_prompt = f"""
    Continue a Discord conversation naturally based on this detailed analysis.
    
    CHANNEL CONTEXT AND THEMES:
    This appears to be a community focused on: {channel_focus}
    Recurring themes in this channel: {recurring_themes_text}
    
    CONVERSATION ANALYSIS:
    Topic clusters identified: {', '.join(topic_clusters_text[:3]) if topic_clusters_text else 'Use your own analysis'}
    
    Open questions from conversation:
    {open_questions_text[0] if open_questions_text else 'None specifically identified'}
    
    User interests and expertise:
    {user_interests_formatted}
    
    Engagement hotspots: {', '.join(engagement_hotspots_text[:2]) if engagement_hotspots_text else 'None specifically identified'}
    
    Continuation opportunities:
    {continuation_formatted}
    
    Optimal approach: {extract_text(optimal_approach) if optimal_approach else 'Use your judgment'}
    
    Specific references you could make:
    {references_formatted}
    
    Key participants to potentially mention: {', '.join([str(p) for p in key_participants]) if key_participants else 'None specifically'}
    
    Relevance to channel themes: {relevance_check if relevance_check else "Ensure your response relates to the channel's typical discussions"}
    
    INSTRUCTIONS FOR GENERATING A THEME-RELEVANT, DISCUSSION-DRIVING RESPONSE:
    1. STAY ON THEME - your response MUST relate to the recurring themes of this community
    2. If someone recently replied to another person, direct your message to them specifically
    3. Continue an EXISTING conversation thread rather than starting a totally new topic
    4. ALWAYS include a question that demands a response - make it specific and interesting
    5. Be opinionated but not confrontational - give people something to agree or disagree with
    6. Reference specific details from their previous messages to show you're paying attention
    7. Keep it conversational, relaxed and casual - like you're chatting with friends
    8. If someone mentioned something they're working on, show genuine interest and ask details
    9. Present a slightly controversial or debatable point related to the channel's typical topics
    10. Ask about personal experiences related to the community's focus area
    11. AVOID generic comments that don't require responses
    12. NEVER suggest topics that are wildly different from what this community typically discusses
    13. NEVER acknowledge that the conversation has been quiet or inactive
    14. IMPORTANT: DO NOT reference commands or bot interactions
    15. DO NOT refer to previous conversations with yourself
    16. DO NOT mention anything about "/spark" or any other command
    17. Speak as if you're genuinely interested in the topic, not as if you were instructed to start a conversation
    18. If you can't find a good topic from the channel history, start a natural topic related to the channel focus
    
    YOUR RESPONSE MUST:
    1. Be RELEVANT to what this community typically talks about
    2. PROVOKE DISCUSSION by demanding a response
    3. Feel like a natural continuation of existing conversations
    4. Address specific people by name if they're engaged in an active thread
    5. NOT reference bot commands or instructions
    """
    
    # Add topic constraint if provided by admin
    if topic:
        starter_prompt += f"\n\nIMPORTANT: Focus specifically on {topic} and relate it to the previous discussions if possible."
    
    context.append({
        "role": "user",
        "content": starter_prompt
    })
    
    # Add a human-like typing delay
    typing_time = random.uniform(2.0, 4.0)
    
    async with channel.typing():
        # Artificial delay to make it feel more natural
        await asyncio.sleep(typing_time)
        
        # Get AI response
        conversation_starter = ai_handler.get_response(context)
        
        # Send the conversation starter if generated
        if conversation_starter and conversation_starter.strip():
            await channel.send(conversation_starter)
            
            # Record bot's message in the tracker
            message_tracker.add_bot_response(interaction.user.id, conversation_starter, channel.id)
            
            # Confirm success to admin (ephemeral)
            await interaction.followup.send("conversation continued successfully", ephemeral=True)
        else:
            # Let the user know if AI failed to generate a response
            await interaction.followup.send("couldn't generate a continuation, try again or provide a specific topic", ephemeral=True)

@bot.tree.command(name="modstats", description="View moderation statistics and manage user violations")
@app_commands.describe(
    user="User to check moderation stats for (optional)",
    reset="Reset the user's violation history (admin only)"
)
async def moderation_stats(interaction: discord.Interaction, user: discord.Member = None, reset: bool = False):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    
    # If we don't have the violation_history attribute yet, initialize it
    if not hasattr(decision_engine, 'violation_history'):
        decision_engine.violation_history = {}
    
    # Reset user's violation history if requested
    if user and reset:
        if user.id in decision_engine.violation_history:
            decision_engine.violation_history[user.id] = []
            await interaction.response.send_message(f"Violation history for {user.display_name} has been reset", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.display_name} has no recorded violations", ephemeral=True)
        return
    
    # If specific user requested, show their stats
    if user:
        if user.id in decision_engine.violation_history and decision_engine.violation_history[user.id]:
            violations = decision_engine.violation_history[user.id]
            
            # Format the violations
            violations_text = ""
            for i, v in enumerate(violations):
                timestamp = datetime.fromtimestamp(v['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                violations_text += f"{i+1}. **{v['rule_violated']}** ({v['severity']} severity) - {timestamp}\n"
            
            stats = f"**Moderation Stats for {user.display_name}**\n\n"
            stats += f"Total violations: {len(violations)}\n"
            stats += f"Recent (24h): {decision_engine.get_violation_count(user.id, hours=24)}\n\n"
            stats += f"**Violation History:**\n{violations_text}"
            
            await interaction.response.send_message(stats, ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.display_name} has no recorded violations", ephemeral=True)
        return
    
    # If no specific user, show overall stats
    total_violations = 0
    user_count = 0
    recent_violations = 0
    
    for user_id, violations in decision_engine.violation_history.items():
        if violations:
            user_count += 1
            total_violations += len(violations)
            
            # Count recent violations (24h)
            cutoff_time = time.time() - (24 * 3600)
            recent = sum(1 for v in violations if v['timestamp'] >= cutoff_time)
            recent_violations += recent
    
    stats = "**Moderation Statistics**\n\n"
    stats += f"Total violations: {total_violations}\n"
    stats += f"Users with violations: {user_count}\n"
    stats += f"Violations in last 24h: {recent_violations}\n\n"
    stats += f"Use `/modstats user:@username` to view specific user stats\n"
    stats += f"Use `/modstats user:@username reset:True` to reset a user's violation history"
    
    await interaction.response.send_message(stats, ephemeral=True)

@bot.tree.command(name="modconfig", description="Configure moderation settings")
@app_commands.describe(
    enabled="Enable or disable moderation",
    auto_timeout="Enable or disable automatic timeouts for repeat offenders",
    warning_threshold="Number of warnings before more severe action",
    timeout_minutes="Minutes to timeout a user after exceeding threshold"
)
async def moderation_config(
    interaction: discord.Interaction, 
    enabled: bool = None,
    auto_timeout: bool = None,
    warning_threshold: int = None,
    timeout_minutes: int = None
):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    
    # Initialize response message
    changes = []
    
    # Handle moderation enable/disable
    if enabled is not None:
        BOT_CONFIG['moderation']['enabled'] = enabled
        changes.append(f"Moderation {'enabled' if enabled else 'disabled'}")
    
    # Handle auto timeout toggle
    if auto_timeout is not None:
        BOT_CONFIG['moderation']['auto_timeout'] = auto_timeout
        changes.append(f"Auto-timeout {'enabled' if auto_timeout else 'disabled'}")
    
    # Handle warning threshold
    if warning_threshold is not None:
        if 1 <= warning_threshold <= 10:
            BOT_CONFIG['moderation']['warning_threshold'] = warning_threshold
            changes.append(f"Warning threshold set to {warning_threshold}")
        else:
            await interaction.response.send_message("Warning threshold must be between 1 and 10", ephemeral=True)
            return
    
    # Handle timeout duration
    if timeout_minutes is not None:
        if 1 <= timeout_minutes <= 60:
            BOT_CONFIG['moderation']['timeout_minutes'] = timeout_minutes
            changes.append(f"Timeout duration set to {timeout_minutes} minutes")
        else:
            await interaction.response.send_message("Timeout duration must be between 1 and 60 minutes", ephemeral=True)
            return
    
    # If no changes specified, show current settings
    if not changes:
        settings = "**Current Moderation Settings**\n\n"
        settings += f"Moderation enabled: {BOT_CONFIG['moderation']['enabled']}\n"
        settings += f"Auto-timeout enabled: {BOT_CONFIG['moderation']['auto_timeout']}\n"
        settings += f"Warning threshold: {BOT_CONFIG['moderation']['warning_threshold']}\n"
        settings += f"Timeout duration: {BOT_CONFIG['moderation']['timeout_minutes']} minutes\n"
        
        # Show log channel if set
        log_channel_id = BOT_CONFIG['moderation'].get('log_channel_id')
        if log_channel_id:
            log_channel = bot.get_channel(int(log_channel_id))
            settings += f"Log channel: {log_channel.mention if log_channel else 'Unknown channel'}\n"
        else:
            settings += f"Log channel: Not set (use /setlogchannel)\n"
            
        # Show warning auto-delete setting
        warning_delete_seconds = BOT_CONFIG['moderation'].get('warning_delete_seconds', 0)
        if warning_delete_seconds > 0:
            settings += f"Warning delete after: {warning_delete_seconds} seconds\n"
        else:
            settings += f"Warning delete: Disabled\n"
            
        settings += f"\nUse `/modconfig` with parameters to change settings."
        
        await interaction.response.send_message(settings, ephemeral=True)
        return
    
    # Confirm changes
    await interaction.response.send_message(f"Moderation settings updated: {', '.join(changes)}", ephemeral=True)

@bot.tree.command(name="setlogchannel", description="Set the channel for logging moderation actions")
@app_commands.describe(
    channel="The channel where moderation logs should be sent",
    delete_violations="Whether to delete rule-violating messages",
    warning_delete_seconds="Time in seconds before deleting warning messages (0 = don't delete)"
)
async def set_log_channel(
    interaction: discord.Interaction, 
    channel: discord.TextChannel = None,
    delete_violations: bool = None,
    warning_delete_seconds: int = None
):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("you need admin perms for that", ephemeral=True)
        return
    
    # Initialize response message
    changes = []
    
    # Set log channel
    if channel is not None:
        BOT_CONFIG['moderation']['log_channel_id'] = channel.id
        changes.append(f"Log channel set to {channel.mention}")
        
        # Send test message to confirm permissions
        try:
            embed = discord.Embed(
                title="Moderation Log Setup",
                description="This channel has been set up to receive moderation logs.",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Setup By", value=interaction.user.mention, inline=False)
            embed.set_footer(text="Sol Moderation System")
            
            await channel.send(embed=embed)
        except Exception as e:
            # Warn if we couldn't send to the channel
            await interaction.followup.send(f"Warning: Failed to send test message to {channel.mention}. Make sure I have permission to send messages and embeds there.", ephemeral=True)
    
    # Set delete_violations flag
    if delete_violations is not None:
        BOT_CONFIG['moderation']['delete_violations'] = delete_violations
        changes.append(f"{'Enabled' if delete_violations else 'Disabled'} automatic deletion of rule-violating messages")
    
    # Set warning_delete_seconds
    if warning_delete_seconds is not None:
        if 0 <= warning_delete_seconds <= 300:  # Max 5 minutes
            BOT_CONFIG['moderation']['warning_delete_seconds'] = warning_delete_seconds
            if warning_delete_seconds > 0:
                changes.append(f"Warning messages will auto-delete after {warning_delete_seconds} seconds")
            else:
                changes.append(f"Warning messages will not auto-delete")
        else:
            await interaction.response.send_message("Warning delete time must be between 0 and 300 seconds", ephemeral=True)
            return
    
    # If no changes were made, show current settings
    if not changes:
        settings = "**Current Moderation Log Settings**\n\n"
        
        # Show log channel if set
        log_channel_id = BOT_CONFIG['moderation'].get('log_channel_id')
        if log_channel_id:
            log_channel = bot.get_channel(int(log_channel_id))
            settings += f"Log channel: {log_channel.mention if log_channel else 'Unknown channel'}\n"
        else:
            settings += f"Log channel: Not set\n"
        
        # Show delete_violations setting
        delete_violations = BOT_CONFIG['moderation'].get('delete_violations', True)
        settings += f"Delete rule violations: {'Enabled' if delete_violations else 'Disabled'}\n"
        
        # Show warning_delete_seconds setting
        warning_delete_seconds = BOT_CONFIG['moderation'].get('warning_delete_seconds', 0)
        if warning_delete_seconds > 0:
            settings += f"Warning delete after: {warning_delete_seconds} seconds\n"
        else:
            settings += f"Warning delete: Disabled\n"
        
        await interaction.response.send_message(settings, ephemeral=True)
        return
    
    # Confirm changes
    await interaction.response.send_message(f"Moderation log settings updated: {', '.join(changes)}", ephemeral=True)

@bot.tree.command(name="exemptrolefrommod", description="Exempt roles from moderation")
@app_commands.describe(
    role="The role to exempt from moderation",
    action="Whether to add or remove the role from exemption list"
)
async def exempt_role(
    interaction: discord.Interaction, 
    role: discord.Role,
    action: typing.Literal["add", "remove"]
):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("You need admin permissions to use this command", ephemeral=True)
        return
    
    # Get current exempt roles
    exempt_roles = BOT_CONFIG.get('moderation', {}).get('exempt_roles', [])
    
    if action == "add":
        # Check if role is already exempt
        if role.id in exempt_roles:
            await interaction.response.send_message(f"Role '{role.name}' is already exempt from moderation", ephemeral=True)
            return
        
        # Add role to exempt list
        exempt_roles.append(role.id)
        BOT_CONFIG['moderation']['exempt_roles'] = exempt_roles
        save_config()
        
        await interaction.response.send_message(f"Role '{role.name}' is now exempt from moderation", ephemeral=True)
    else:
        # Check if role is in exempt list
        if role.id not in exempt_roles:
            await interaction.response.send_message(f"Role '{role.name}' is not in the exemption list", ephemeral=True)
            return
        
        # Remove role from exempt list
        exempt_roles.remove(role.id)
        BOT_CONFIG['moderation']['exempt_roles'] = exempt_roles
        save_config()
        
        await interaction.response.send_message(f"Role '{role.name}' has been removed from the moderation exemption list", ephemeral=True)

@bot.tree.command(name="listexemptroles", description="List roles that are exempt from moderation")
async def list_exempt_roles(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("You need admin permissions to use this command", ephemeral=True)
        return
    
    # Get current exempt roles
    exempt_roles = BOT_CONFIG.get('moderation', {}).get('exempt_roles', [])
    
    if not exempt_roles:
        await interaction.response.send_message("No roles are currently exempt from moderation", ephemeral=True)
        return
    
    # Get role names from IDs
    role_names = []
    for role_id in exempt_roles:
        role = interaction.guild.get_role(role_id)
        if role:
            role_names.append(f"• {role.name}")
    
    # Create embed
    embed = discord.Embed(
        title="Roles Exempt from Moderation",
        description="\n".join(role_names) if role_names else "No valid roles found",
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Function to save config changes
def save_config():
    """Save the current configuration to config.py file"""
    try:
        # Read the current file content
        with open("config.py", "r") as file:
            content = file.read()
        
        # Replace the BOT_CONFIG section
        import re
        import json
        
        # Convert BOT_CONFIG to valid Python code representation
        config_str = "BOT_CONFIG = {\n"
        for key, value in BOT_CONFIG.items():
            if isinstance(value, dict):
                config_str += f"    '{key}': {{\n"
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, list):
                        # Handle lists with proper formatting
                        list_str = "[\n"
                        for item in sub_value:
                            if isinstance(item, str):
                                list_str += f'            "{item}",\n'
                            else:
                                list_str += f"            {item},\n"
                        list_str += "        ]"
                        config_str += f"        '{sub_key}': {list_str},\n"
                    elif isinstance(sub_value, str):
                        config_str += f"        '{sub_key}': '{sub_value}',\n"
                    else:
                        config_str += f"        '{sub_key}': {sub_value},\n"
                config_str += "    },\n"
            elif isinstance(value, list):
                # Handle lists with proper formatting
                list_str = "[\n"
                for item in value:
                    if isinstance(item, str):
                        list_str += f'        "{item}",\n'
                    else:
                        list_str += f"        {item},\n"
                list_str += "    ]"
                config_str += f"    '{key}': {list_str},\n"
            elif isinstance(value, str):
                config_str += f"    '{key}': '{value}',\n"
            else:
                config_str += f"    '{key}': {value},\n"
        config_str += "}"
        
        # Use regex to replace the BOT_CONFIG section
        pattern = r'BOT_CONFIG\s*=\s*\{[^}]*\}'
        updated_content = re.sub(pattern, config_str, content, flags=re.DOTALL)
        
        # Write the updated content back to the file
        with open("config.py", "w") as file:
            file.write(updated_content)
            
        print("Configuration saved successfully")
    except Exception as e:
        print(f"Error saving configuration: {e}")

# Add the natural command processor function
async def process_natural_command(message):
    # Check if user has permission to use commands (mod permissions or allowed role)
    has_permission = False
    permission_reason = ""
    
    # First check built-in permissions
    if message.author.guild_permissions.manage_messages:
        has_permission = True
        permission_reason = "manage_messages permission"
    elif message.author.guild_permissions.administrator:
        has_permission = True
        permission_reason = "administrator permission"
    else:
        # Check if user has any of the allowed roles
        command_roles = BOT_CONFIG.get('command_roles', [])
        if command_roles:
            for role in message.author.roles:
                if role.id in command_roles:
                    has_permission = True
                    permission_reason = f"authorized role: {role.name}"
                    break
        else:
            # If no command_roles are set, only admins can use commands
            has_permission = False  # Explicitly deny permission if no authorized roles
            permission_reason = "no authorized roles set - only admins can use commands"
    
    # If no permission, log and silently ignore
    if not has_permission:
        print(f"Command ignored - {message.author.name} tried to use command '{message.content}' but lacks permission. Reason: {permission_reason}")
        return
    
    print(f"Command accepted - {message.author.name} using '{message.content}'. Permission: {permission_reason}")
    
    # Extract the command part (remove ".sol " prefix)
    command_text = message.content[5:].strip()
    
    # Create AI prompt to understand the command
    command_prompt = f"""
    You are a Discord bot named Sol. A moderator has issued you a command in natural language. 
    Parse this command and determine what action to take.
    
    COMMAND: "{command_text}"
    
    Identify the command type from these categories:
    1. WARNING - Warning a user about their behavior
    2. MUTE - Temporarily muting/timing out a user
    3. MESSAGE - Sending a message to a specific user or channel
    4. SEARCH - Searching for information and reporting back
    5. BAN - Banning a user
    6. KICK - Kicking a user from the server
    7. REPLY - Reply to a specific message by ID
    8. OTHER - Any other type of command
    9. GITHUB_ISSUE - Creating an issue on GitHub
    
    Extract these details:
    - Command Type: (from the list above)
    - Target User: (username or mention of who to act upon, if any)
    - Target Channel: (channel name or mention to send messages to, if any - include the # symbol if present)
    - Duration: (for temporary actions like mutes, if specified)
    - Reason: (for warnings/mutes/etc) or Message Content (for MESSAGE/REPLY commands, extract what should be said)
    - Search Query: (what to search for, if it's a search command)
    - Message ID: (for REPLY commands, the ID of the message to reply to)
    - Issue Title: (for GITHUB_ISSUE commands, extract a concise title for the issue)
    - Issue Body: (for GITHUB_ISSUE commands, extract the detailed description)
    - Repository: (for GITHUB_ISSUE commands, extract the repository name if specified, otherwise leave as null)
    
    For MESSAGE and REPLY commands, the Reason field should contain the exact message content that should be delivered, not the reason for the message.
    
    Examples of MESSAGE commands and their expected extraction:
    - "say hello to @user" → Message content: "hello"
    - "say to @user to take a walk" → Message content: "take a walk"
    - "tell #channel that I'm going to be away" → Message content: "I'm going to be away"
    - "say good night to everyone" → Message content: "good night"
    
    Examples of REPLY commands and their expected extraction:
    - "reply to message id 1234567890 and say hello" → Message content: "hello", Message ID: "1234567890"
    - "reply to this message id 1234567890 with thanks for your help" → Message content: "thanks for your help", Message ID: "1234567890"
    - "respond to message 1234567890 in #general saying I agree" → Message content: "I agree", Message ID: "1234567890", Target Channel: "#general"
    
    Examples of GITHUB_ISSUE commands and their expected extraction:
    - "open this as an issue on github: UI bug in login screen" → Issue Title: "UI bug in login screen", Issue Body: "UI bug in login screen"
    - "create github issue about network timeout errors in the API" → Issue Title: "Network timeout errors in the API", Issue Body: "Network timeout errors in the API"
    - "open this as a github issue in org/repo: Feature request for dark mode" → Issue Title: "Feature request for dark mode", Issue Body: "Feature request for dark mode", Repository: "org/repo"
    
    Respond with ONLY a JSON object like this (no explanation):
    
    {{"command_type": "COMMAND_TYPE", "target_user": "USERNAME", "target_channel": "CHANNEL", "duration": "DURATION", "reason": "REASON", "search_query": "QUERY", "message_id": "MESSAGE_ID", "issue_title": "ISSUE TITLE", "issue_body": "ISSUE BODY", "repository": "REPOSITORY"}}
    
    Use null for any fields that don't apply.
    DO NOT wrap your response in markdown code blocks or backticks.
    """
    
    # Use AI to understand the command
    try:
        payload = {
            "model": "google/gemini-2.5-flash-preview",
            "messages": [{
                "role": "user", 
                "content": command_prompt
            }],
            "max_tokens": 500
        }
        
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                ai_response = data["choices"][0]["message"]["content"].strip()
                
                # Clean up the response - remove any markdown code block wrappers
                # Remove ```json and ``` markers that might surround the JSON
                ai_response = ai_response.replace("```json", "").replace("```", "").strip()
                
                # Check if the response might still contain backticks
                if ai_response.startswith("`") and ai_response.endswith("`"):
                    ai_response = ai_response[1:-1].strip()
                
                print(f"Cleaned AI response: {ai_response}")
                
                # Extract JSON from the response
                try:
                    command_data = json.loads(ai_response)
                    
                    # Now execute the command based on its type
                    await execute_command(message, command_data)
                except json.JSONDecodeError as e:
                    print(f"Failed to parse AI response as JSON: {ai_response}")
                    print(f"JSON error: {str(e)}")
                    # Try a fallback approach - look for { and } and extract what's between them
                    try:
                        start_idx = ai_response.find('{')
                        end_idx = ai_response.rfind('}')
                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            json_str = ai_response[start_idx:end_idx+1]
                            command_data = json.loads(json_str)
                            await execute_command(message, command_data)
                        else:
                            await message.reply("Sorry, I couldn't understand that command.", mention_author=False)
                    except Exception as e2:
                        print(f"Fallback JSON extraction failed: {str(e2)}")
                        await message.reply("Sorry, I couldn't understand that command.", mention_author=False)
        else:
            print(f"AI API error: {response.status_code}, {response.text}")
            await message.reply("Sorry, I encountered an error processing your command.", mention_author=False)
    except Exception as e:
        print(f"Error processing natural command: {e}")
        await message.reply("Sorry, something went wrong while processing your command.", mention_author=False)

async def execute_command(message, command_data):
    command_type = command_data.get("command_type")
    target_user_name = command_data.get("target_user")
    target_channel_name = command_data.get("target_channel")
    duration = command_data.get("duration")
    reason = command_data.get("reason")
    search_query = command_data.get("search_query")
    message_id = command_data.get("message_id")
    # GitHub issue fields
    issue_title = command_data.get("issue_title")
    issue_body = command_data.get("issue_body")
    repository = command_data.get("repository")
    
    # Find target user if specified
    target_user = None
    if target_user_name:
        # Try to find the user by mention or name
        if target_user_name.startswith("<@") and target_user_name.endswith(">"):
            # Extract user ID from mention
            user_id = ''.join(filter(str.isdigit, target_user_name))
            target_user = message.guild.get_member(int(user_id)) if user_id else None
        else:
            # Search by name
            for member in message.guild.members:
                if target_user_name.lower() in member.display_name.lower() or (
                    member.nick and target_user_name.lower() in member.nick.lower()):
                    target_user = member
                    break
    
    # Find target channel if specified
    target_channel = message.channel  # Default to current channel
    if target_channel_name:
        # Try to find the channel by mention or name
        if target_channel_name.startswith("<#") and target_channel_name.endswith(">"):
            # Extract channel ID from mention
            channel_id = ''.join(filter(str.isdigit, target_channel_name))
            found_channel = message.guild.get_channel(int(channel_id)) if channel_id else None
            if found_channel:
                target_channel = found_channel
        elif target_channel_name.startswith("#"):
            # Search by name with # prefix
            channel_name = target_channel_name[1:]  # Remove the # symbol
            for channel in message.guild.text_channels:
                if channel_name.lower() == channel.name.lower():
                    target_channel = channel
                    break
        else:
            # Search by name without # prefix
            for channel in message.guild.text_channels:
                if target_channel_name.lower() == channel.name.lower():
                    target_channel = channel
                    break
    
    # Convert duration to seconds if specified
    timeout_seconds = 0
    if duration:
        try:
            # Parse time format like "5m", "1h", "30s"
            duration_lower = duration.lower()
            if "m" in duration_lower:
                timeout_seconds = int(''.join(filter(str.isdigit, duration_lower))) * 60
            elif "h" in duration_lower:
                timeout_seconds = int(''.join(filter(str.isdigit, duration_lower))) * 3600
            elif "d" in duration_lower:
                timeout_seconds = int(''.join(filter(str.isdigit, duration_lower))) * 86400
            elif "s" in duration_lower:
                timeout_seconds = int(''.join(filter(str.isdigit, duration_lower)))
            else:
                # Default to minutes if unit not specified
                timeout_seconds = int(''.join(filter(str.isdigit, duration_lower))) * 60
        except ValueError:
            timeout_seconds = 300  # Default 5 minutes if parsing fails
    
    # Execute the command based on type
    try:
        if command_type == "WARNING":
            if target_user:
                # Rephrase warning in casual, conversational style
                try:
                    # Create a prompt to rephrase the warning
                    warning_reason = reason or "their behavior"
                    rephrase_prompt = f"""
                    Rephrase this moderation warning in an extremely casual, human-like Discord style.
                    Make it sound like a real person (not a bot) is naturally expressing mild disapproval.
                    
                    Don't use formal warning language, bot-like phrases, or structured formats.
                    No "Warning:", no bold text, no ⚠️ emoji, no "has been warned" phrasing.
                    
                    Instead use very casual language like:
                    - "hey maybe tone it down a bit"
                    - "yo chill with that lol"
                    - "probably not the best idea"
                    
                    The warning is about: "{warning_reason}"
                    The user being warned: {target_user.display_name}
                    
                    Just respond with a super casual rephrased warning message, 1-2 short sentences max.
                    Make it sound exactly like how a regular Discord user would casually say it.
                    """
                    
                    payload = {
                        "model": "google/gemini-2.5-flash-preview",
                        "messages": [{
                            "role": "user", 
                            "content": rephrase_prompt
                        }],
                        "max_tokens": 150
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            rephrased_warning = data["choices"][0]["message"]["content"].strip()
                            # Send the casually rephrased warning
                            await target_channel.send(f"{target_user.mention} {rephrased_warning}")
                        else:
                            # Fallback if AI fails - use more casual default
                            await target_channel.send(f"hey {target_user.mention}, mind toning it down a bit? {warning_reason}")
                    else:
                        # Fallback if API fails
                        await target_channel.send(f"hey {target_user.mention}, mind toning it down a bit? {warning_reason}")
                except Exception as e:
                    print(f"Error rephrasing warning: {e}")
                    # Fallback if anything fails
                    await target_channel.send(f"hey {target_user.mention}, mind toning it down a bit? {warning_reason}")
                
                await message.add_reaction("✅")
            else:
                await message.reply("I couldn't find that user to warn.", mention_author=False)
        
        elif command_type == "MUTE":
            if target_user:
                # Calculate timeout duration (use Discord's timeout feature)
                timeout_duration = None
                if timeout_seconds > 0:
                    timeout_duration = datetime.now() + timedelta(seconds=timeout_seconds)
                else:
                    # Default timeout for 5 minutes if no duration specified
                    timeout_duration = datetime.now() + timedelta(minutes=5)
                
                # Apply timeout to user
                await target_user.timeout(timeout_duration, reason=reason or "Requested by moderator")
                
                # Determine duration string for the prompt
                duration_str = f"{timeout_seconds//60} minutes" if timeout_seconds else "5 minutes"
                
                # Rephrase timeout message in casual style
                try:
                    # Create a prompt to rephrase the mute message
                    mute_reason = reason or "breaking the rules"
                    rephrase_prompt = f"""
                    Rephrase this timeout notification in an extremely casual, human-like Discord style.
                    Make it sound like a real person (not a bot) is naturally mentioning that someone got muted.
                    
                    Don't use formal moderation language, bot-like phrases, or structured formats.
                    No "Muted:", no bold text, no formal tone, no "has been muted" phrasing.
                    
                    Instead use very casual language like:
                    - "taking a little break from the chat for a bit"
                    - "gonna have to chill in timeout for a while"
                    - "maybe time to take a breather"
                    
                    The timeout is for: {duration_str}
                    The reason: "{mute_reason}"
                    The user being timed out: {target_user.display_name}
                    
                    Just respond with a super casual rephrased timeout message, 1-2 short sentences max.
                    Make it sound exactly like how a regular Discord user would casually mention it.
                    """
                    
                    payload = {
                        "model": "google/gemini-2.5-flash-preview",
                        "messages": [{
                            "role": "user", 
                            "content": rephrase_prompt
                        }],
                        "max_tokens": 150
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            rephrased_mute = data["choices"][0]["message"]["content"].strip()
                            # Send the casually rephrased mute message
                            await target_channel.send(f"{target_user.mention} {rephrased_mute}")
                        else:
                            # Fallback if AI fails - use more casual default
                            await target_channel.send(f"looks like {target_user.mention} is taking a {duration_str} break from chat. {mute_reason}")
                    else:
                        # Fallback if API fails
                        await target_channel.send(f"looks like {target_user.mention} is taking a {duration_str} break from chat. {mute_reason}")
                except Exception as e:
                    print(f"Error rephrasing mute message: {e}")
                    # Fallback if anything fails
                    await target_channel.send(f"looks like {target_user.mention} is taking a {duration_str} break from chat. {mute_reason}")
                
                await message.add_reaction("✅")
            else:
                await message.reply("I couldn't find that user to mute.", mention_author=False)
        
        elif command_type == "MESSAGE":
            # Send message to the target channel
            if target_user:
                # Message to specific user that needs to be rephrased
                message_content = reason  # The reason field contains the message content
                
                if message_content:
                    # Use AI to rephrase in Sol's casual style
                    try:
                        # Create a prompt to rephrase the message
                        rephrase_prompt = f"""
                        Rephrase this message in your own casual, conversational Discord style. 
                        Keep it natural as if you're just having a regular chat, not following orders.
                        Don't indicate that you're following instructions or that a moderator asked you to say something.
                        Use casual language, maybe some lowercase, and make it sound authentic.
                        
                        IMPORTANT: DO NOT include the user's name or mention at the beginning of your message. 
                        The system will automatically add the mention, so just focus on the message content.
                        
                        Message to rephrase: "{message_content}"
                        
                        Target user: {target_user.display_name}
                        
                        Just respond with the rephrased message directly, no explanations.
                        """
                        
                        payload = {
                            "model": "google/gemini-2.5-flash-preview",
                            "messages": [{
                                "role": "user", 
                                "content": rephrase_prompt
                            }],
                            "max_tokens": 500
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                            "Content-Type": "application/json"
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=5
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if "choices" in data and len(data["choices"]) > 0:
                                rephrased_message = data["choices"][0]["message"]["content"].strip()
                                
                                # Remove user mentions from the start of the message if they exist
                                # Check for direct mention format: @username
                                if rephrased_message.lower().startswith(f"@{target_user.display_name.lower()}"):
                                    rephrased_message = rephrased_message[len(target_user.display_name) + 1:].strip()
                                # Check for raw mention format: <@userid>
                                elif rephrased_message.startswith(f"<@{target_user.id}>"):
                                    rephrased_message = rephrased_message[len(f"<@{target_user.id}>"):].strip()
                                # Check if message starts with the username without @
                                elif rephrased_message.lower().startswith(target_user.display_name.lower()):
                                    rephrased_message = rephrased_message[len(target_user.display_name):].strip()
                                
                                # Remove common punctuation after a mention
                                if rephrased_message.startswith(", "):
                                    rephrased_message = rephrased_message[2:].strip()
                                elif rephrased_message.startswith(","):
                                    rephrased_message = rephrased_message[1:].strip()
                                
                                # Send the rephrased message that mentions the user
                                await target_channel.send(f"{target_user.mention} {rephrased_message}")
                            else:
                                # Fallback if AI fails
                                await target_channel.send(f"{target_user.mention} {message_content}")
                        else:
                            # Fallback if API fails
                            await target_channel.send(f"{target_user.mention} {message_content}")
                    except Exception as e:
                        print(f"Error rephrasing message: {e}")
                        # Fallback if anything fails
                        await target_channel.send(f"{target_user.mention} {message_content}")
                else:
                    # Generic fallback message if no reason provided
                    await target_channel.send(f"{target_user.mention} hey, what's up?")
            else:
                # Message to a channel that needs to be rephrased
                message_content = reason  # The reason field contains the message content
                
                if message_content:
                    # Use AI to rephrase in Sol's casual style
                    try:
                        # Create a prompt to rephrase the message
                        rephrase_prompt = f"""
                        Rephrase this message in your own casual, conversational Discord style. 
                        Keep it natural as if you're just having a regular chat, not following orders.
                        Don't indicate that you're following instructions or that a moderator asked you to say something.
                        Use casual language, maybe some lowercase, and make it sound authentic.
                        
                        Message to rephrase: "{message_content}"
                        
                        Target channel: {target_channel.name}
                        
                        Just respond with the rephrased message directly, no explanations.
                        """
                        
                        payload = {
                            "model": "google/gemini-2.5-flash-preview",
                            "messages": [{
                                "role": "user", 
                                "content": rephrase_prompt
                            }],
                            "max_tokens": 500
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                            "Content-Type": "application/json"
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=5
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if "choices" in data and len(data["choices"]) > 0:
                                rephrased_message = data["choices"][0]["message"]["content"].strip()
                                # Send the rephrased message to the channel
                                await target_channel.send(rephrased_message)
                            else:
                                # Fallback if AI fails
                                await target_channel.send(message_content)
                        else:
                            # Fallback if API fails
                            await target_channel.send(message_content)
                    except Exception as e:
                        print(f"Error rephrasing message: {e}")
                        # Fallback if anything fails
                        await target_channel.send(message_content)
                else:
                    # Generic message if no reason provided
                    await target_channel.send("hey everyone, what's up?")
            
            await message.add_reaction("✅")
        
        elif command_type == "SEARCH":
            if search_query:
                # Send typing indicator to show activity
                async with target_channel.typing():
                    # Use AI to perform search and summarize
                    search_prompt = f"""
                    Search the internet for: "{search_query}"
                    
                    Provide a conversational, helpful summary of the information in a casual style. 
                    Include relevant details but keep it concise (max 2-3 paragraphs).
                    Sound like you're naturally chatting in Discord, not providing a formal report.
                    """
                    
                    try:
                        # Use the online-capable model for search
                        payload = {
                            "model": ai_handler.online_model,
                            "messages": [{
                                "role": "user", 
                                "content": search_prompt
                            }],
                            "max_tokens": 1000
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                            "Content-Type": "application/json"
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=15  # Give it more time for search
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if "choices" in data and len(data["choices"]) > 0:
                                search_result = data["choices"][0]["message"]["content"].strip()
                                
                                # Send message with search result
                                if target_user:
                                    await target_channel.send(f"{target_user.mention} Here's what I found about '{search_query}':\n\n{search_result}")
                                else:
                                    await target_channel.send(f"Here's what I found about '{search_query}':\n\n{search_result}")
                                
                                await message.add_reaction("✅")
                            else:
                                await message.reply("I couldn't find any information on that topic.", mention_author=False)
                        else:
                            await message.reply("Sorry, I had trouble searching for that information.", mention_author=False)
                    except Exception as e:
                        print(f"Error during search command: {e}")
                        await message.reply("I encountered an error while searching.", mention_author=False)
            else:
                await message.reply("I need to know what to search for.", mention_author=False)
        
        elif command_type == "BAN":
            if target_user and message.author.guild_permissions.ban_members:
                # Get user's display name before banning
                user_name = target_user.display_name
                
                # Ban the user
                await message.guild.ban(target_user, reason=reason or "Requested by moderator", delete_message_days=1)
                
                # Rephrase ban message in casual style
                try:
                    # Create a prompt to rephrase the ban message
                    ban_reason = reason or "breaking server rules"
                    rephrase_prompt = f"""
                    Rephrase this ban announcement in an extremely casual, human-like Discord style.
                    Make it sound like a real person (not a bot) is naturally mentioning that someone got banned.
                    
                    Don't use formal moderation language, bot-like phrases, or structured formats.
                    No "Banned:", no bold text, no formal tone, no "has been banned" phrasing.
                    
                    Instead use very casual language like:
                    - "won't be coming back anytime soon"
                    - "just got yeeted from the server"
                    - "had to go, was breaking too many rules"
                    
                    The reason: "{ban_reason}"
                    The user being banned: {user_name}
                    
                    Just respond with a super casual rephrased ban message, 1-2 short sentences max.
                    Make it sound exactly like how a regular Discord user would casually mention it.
                    """
                    
                    payload = {
                        "model": "google/gemini-2.5-flash-preview",
                        "messages": [{
                            "role": "user", 
                            "content": rephrase_prompt
                        }],
                        "max_tokens": 150
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            rephrased_ban = data["choices"][0]["message"]["content"].strip()
                            # Send the casually rephrased ban message
                            await target_channel.send(rephrased_ban)
                        else:
                            # Fallback if AI fails - use more casual default
                            await target_channel.send(f"looks like {user_name} won't be joining us anymore. {ban_reason}")
                    else:
                        # Fallback if API fails
                        await target_channel.send(f"looks like {user_name} won't be joining us anymore. {ban_reason}")
                except Exception as e:
                    print(f"Error rephrasing ban message: {e}")
                    # Fallback if anything fails
                    await target_channel.send(f"looks like {user_name} won't be joining us anymore. {ban_reason}")
                
                await message.add_reaction("✅")
            else:
                if not target_user:
                    await message.reply("I couldn't find that user to ban.", mention_author=False)
                else:
                    await message.reply("You don't have permission to ban members.", mention_author=False)
        
        elif command_type == "KICK":
            if target_user and message.author.guild_permissions.kick_members:
                # Get user's display name before kicking
                user_name = target_user.display_name
                
                # Kick the user
                await message.guild.kick(target_user, reason=reason or "Requested by moderator")
                
                # Rephrase kick message in casual style
                try:
                    # Create a prompt to rephrase the kick message
                    kick_reason = reason or "breaking server rules"
                    rephrase_prompt = f"""
                    Rephrase this kick announcement in an extremely casual, human-like Discord style.
                    Make it sound like a real person (not a bot) is naturally mentioning that someone got kicked.
                    
                    Don't use formal moderation language, bot-like phrases, or structured formats.
                    No "Kicked:", no bold text, no formal tone, no "has been kicked" phrasing.
                    
                    Instead use very casual language like:
                    - "just got booted, but can come back later"
                    - "had to take a hike for a bit"
                    - "got shown the door, maybe next time they'll behave"
                    
                    The reason: "{kick_reason}"
                    The user being kicked: {user_name}
                    
                    Just respond with a super casual rephrased kick message, 1-2 short sentences max.
                    Make it sound exactly like how a regular Discord user would casually mention it.
                    """
                    
                    payload = {
                        "model": "google/gemini-2.5-flash-preview",
                        "messages": [{
                            "role": "user", 
                            "content": rephrase_prompt
                        }],
                        "max_tokens": 150
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            rephrased_kick = data["choices"][0]["message"]["content"].strip()
                            # Send the casually rephrased kick message
                            await target_channel.send(rephrased_kick)
                        else:
                            # Fallback if AI fails - use more casual default
                            await target_channel.send(f"{user_name} just got booted. {kick_reason}")
                    else:
                        # Fallback if API fails
                        await target_channel.send(f"{user_name} just got booted. {kick_reason}")
                except Exception as e:
                    print(f"Error rephrasing kick message: {e}")
                    # Fallback if anything fails
                    await target_channel.send(f"{user_name} just got booted. {kick_reason}")
                
                await message.add_reaction("✅")
            else:
                if not target_user:
                    await message.reply("I couldn't find that user to kick.", mention_author=False)
                else:
                    await message.reply("You don't have permission to kick members.", mention_author=False)
        
        elif command_type == "REPLY":
            # Get the message ID to reply to
            if message_id:
                # Determine which channel to use
                reply_channel = target_channel or message.channel
                
                # Try to find the message to reply to
                try:
                    # Get the message by ID
                    message_to_reply = await reply_channel.fetch_message(int(message_id))
                    
                    # Prepare the reply content
                    reply_content = reason or "👍"
                    
                    # Use AI to rephrase in Sol's casual style
                    try:
                        # Create a prompt to rephrase the message
                        rephrase_prompt = f"""
                        Rephrase this message in your own casual, conversational Discord style. 
                        Keep it natural as if you're just having a regular chat, not following orders.
                        Don't indicate that you're following instructions or that a moderator asked you to say something.
                        Use casual language, maybe some lowercase, and make it sound authentic.
                        
                        Message to rephrase: "{reply_content}"
                        
                        Replying to message: "{message_to_reply.content[:100]}..."
                        
                        Just respond with the rephrased message directly, no explanations.
                        """
                        
                        payload = {
                            "model": "google/gemini-2.5-flash-preview",
                            "messages": [{
                                "role": "user", 
                                "content": rephrase_prompt
                            }],
                            "max_tokens": 500
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                            "Content-Type": "application/json"
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=5
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if "choices" in data and len(data["choices"]) > 0:
                                rephrased_message = data["choices"][0]["message"]["content"].strip()
                                
                                # Send the rephrased reply to the message
                                await message_to_reply.reply(rephrased_message)
                            else:
                                # Fallback if AI fails
                                await message_to_reply.reply(reply_content)
                        else:
                            # Fallback if API fails
                            await message_to_reply.reply(reply_content)
                    except Exception as e:
                        print(f"Error rephrasing reply: {e}")
                        # Fallback if anything fails
                        await message_to_reply.reply(reply_content)
                    
                    await message.add_reaction("✅")
                except discord.NotFound:
                    await message.reply("I couldn't find a message with that ID.", mention_author=False)
                except discord.Forbidden:
                    await message.reply("I don't have permission to view or reply to that message.", mention_author=False)
                except Exception as e:
                    print(f"Error handling reply command: {e}")
                    await message.reply(f"There was an error trying to reply to that message.", mention_author=False)
            else:
                await message.reply("I need a message ID to reply to.", mention_author=False)
        
        elif command_type == "GITHUB_ISSUE":
            # Check if we have a referenced message (reply context)
            referenced_message = None
            if message.reference and message.reference.message_id:
                try:
                    referenced_message = await message.channel.fetch_message(message.reference.message_id)
                except Exception as e:
                    print(f"Error fetching referenced message: {e}")
            
            # Get GitHub credentials from environment
            github_token = os.getenv('GITHUB_TOKEN')
            github_repo = repository or os.getenv('GITHUB_REPO')
            
            if not github_token:
                await message.reply("GitHub integration not configured. Add GITHUB_TOKEN to .env file.", mention_author=False)
                return
                
            if not github_repo:
                await message.reply("GitHub repository not specified. Either mention it in the command or add GITHUB_REPO to .env file.", mention_author=False)
                return
            
            # Collect original content to enhance
            original_content = ""
            related_messages = []
            
            # If this is a reply to another message, gather context from surrounding messages
            if referenced_message:
                # Store the original referenced message
                original_content = referenced_message.content
                original_author = referenced_message.author
                
                # Send typing indicator to show we're working
                async with message.channel.typing():
                    # Fetch message history (40 messages before and after)
                    try:
                        # Collect messages before the referenced message
                        messages_before = []
                        async for msg in message.channel.history(limit=40, before=referenced_message):
                            # Include messages from the same author
                            if msg.author == original_author:
                                messages_before.append({
                                    "content": msg.content,
                                    "time": msg.created_at.strftime("%H:%M:%S")
                                })
                        messages_before.reverse()  # Show in chronological order
                        
                        # Collect messages after the referenced message
                        messages_after = []
                        async for msg in message.channel.history(limit=40, after=referenced_message):
                            if msg.author == original_author and msg.id != message.id:  # Skip command message
                                messages_after.append({
                                    "content": msg.content,
                                    "time": msg.created_at.strftime("%H:%M:%S")
                                })
                        
                        # Combine all collected messages
                        all_related_messages = messages_before + [{"content": original_content, "time": referenced_message.created_at.strftime("%H:%M:%S")}] + messages_after
                        
                        # Filter for relevance to the issue/feature
                        if len(all_related_messages) > 1:
                            # First, use AI to determine which messages are actually related to the same issue
                            filter_prompt = f"""
                            Below are messages from a Discord chat history. The main message (marked with ---MAIN---) describes a bug or feature request.
                            Identify which other messages are directly related to the SAME specific issue/feature.
                            
                            Ignore any messages that:
                            - Are general chitchat
                            - Discuss different features or bugs
                            - Are not directly relevant to understanding the specific issue or request
                            
                            For each message, respond with "YES" only if it is closely related to the main message's issue/feature, otherwise "NO".
                            
                            ---MAIN MESSAGE---
                            {original_content}
                            ---END MAIN MESSAGE---
                            
                            """
                            
                            # Add the other messages to check
                            for i, msg in enumerate(all_related_messages):
                                if msg["content"] != original_content:  # Skip the main message
                                    filter_prompt += f"\nMessage {i+1}: {msg['content']}\n"
                            
                            filter_payload = {
                                "model": "google/gemini-2.5-flash-preview",
                                "messages": [{
                                    "role": "user", 
                                    "content": filter_prompt
                                }],
                                "max_tokens": 500
                            }
                            
                            headers = {
                                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                                "Content-Type": "application/json"
                            }
                            
                            try:
                                filter_response = requests.post(
                                    "https://openrouter.ai/api/v1/chat/completions",
                                    headers=headers,
                                    json=filter_payload,
                                    timeout=10
                                )
                                
                                if filter_response.status_code == 200:
                                    data = filter_response.json()
                                    if "choices" in data and len(data["choices"]) > 0:
                                        filter_result = data["choices"][0]["message"]["content"]
                                        
                                        # Process the filter results
                                        filtered_messages = []
                                        current_index = 0
                                        yes_count = 0
                                        
                                        for i, msg in enumerate(all_related_messages):
                                            if msg["content"] == original_content:
                                                # Always include the main message
                                                filtered_messages.append(msg)
                                            else:
                                                # Try to find the answer for this message
                                                message_id = f"Message {i+1}" if i+1 < len(all_related_messages) else f"Message {i}"
                                                if message_id in filter_result and "YES" in filter_result.split(message_id)[1].split("\n")[0].upper():
                                                    filtered_messages.append(msg)
                                                    yes_count += 1
                                            
                                            # If we found relevant messages, use them, otherwise use all messages
                                            if yes_count > 0:
                                                related_messages = filtered_messages
                                                print(f"Filtered message context: Kept {yes_count} relevant messages out of {len(all_related_messages)-1} total")
                                            else:
                                                # Fallback to all messages if filtering failed
                                                related_messages = all_related_messages
                                else:
                                    # Fallback to all messages if API call failed
                                    related_messages = all_related_messages
                            except Exception as e:
                                print(f"Error filtering related messages: {e}")
                                # Fallback to all messages if exception occurred
                                related_messages = all_related_messages
                        else:
                            related_messages = all_related_messages
                    except Exception as e:
                        print(f"Error collecting message context: {e}")
            elif issue_body:
                original_content = issue_body
            
            if not original_content:
                await message.reply("I need content for the issue. Either reply to a message or include content in your command.", mention_author=False)
                return
            
            # Use AI to enhance the title and description
            try:
                # Send typing indicator to show we're working
                async with message.channel.typing():
                    # Create prompt for AI to generate enhanced title and description
                    enhance_prompt = f"""
                    You are writing a GitHub issue AS THE USER who reported the problem. Transform the following message and related context into a clear, direct GitHub issue that sounds like it came directly from the person experiencing the issue.

                    PRIMARY MESSAGE:
                    {original_content}
                    
                    """
                    
                    # Add related messages if available
                    if related_messages:
                        enhance_prompt += f"RELATED MESSAGES FROM SAME USER:\n"
                        for i, msg in enumerate(related_messages):
                            enhance_prompt += f"[{msg['time']}]: {msg['content']}\n"
                        enhance_prompt += "\n"
                        enhance_prompt += "Focus ONLY on information directly relevant to this specific bug/feature request. Ignore any unrelated topics.\n"
                    
                    enhance_prompt += """
                    Create a concise, technical title that clearly summarizes the issue or feature request.

                    Then write a detailed description IN FIRST PERSON (I/we need, I'm experiencing, etc.) that:
                    1. Is extremely direct and precise - no fluff or third-party perspective
                    2. Uses analytical, factual language focused on the core issue/request
                    3. Clearly separates problem description from proposed solutions
                    4. Includes technical details and exact behaviors where relevant
                    5. Avoids redundancy and overly formal language
                    6. ONLY discusses the specific issue/feature in question - nothing else
                    
                    IMPORTANT STYLE GUIDE:
                    - Write in first person as if you ARE the user
                    - Be direct: "I need X" not "The user would like X"
                    - Be precise: use exact technical terms
                    - Be concrete: describe exactly what happens and what should happen
                    - Use bullet points for clarity when listing multiple items
                    - Format technical terms using code formatting where appropriate
                    - Stay STRICTLY focused on the specific issue/feature
                    
                    Return ONLY a JSON object with these fields:
                    - "title": A clear, technical title (max 80 chars)
                    - "description": A detailed, well-formatted issue description in first person
                    
                    DO NOT include explanations outside the JSON structure.
                    """
                    
                    payload = {
                        "model": "google/gemini-2.5-flash-preview",
                        "messages": [{
                            "role": "user", 
                            "content": enhance_prompt
                        }],
                        "max_tokens": 800
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=10
                    )
                    
                    enhanced_title = "Issue from Discord"
                    enhanced_description = original_content
                    
                    if response.status_code == 200:
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            ai_response = data["choices"][0]["message"]["content"].strip()
                            
                            # Extract JSON from response
                            try:
                                # Find anything that looks like JSON in the response
                                json_start = ai_response.find('{')
                                json_end = ai_response.rfind('}')
                                
                                if json_start != -1 and json_end != -1:
                                    json_str = ai_response[json_start:json_end+1]
                                    enhanced_data = json.loads(json_str)
                                    
                                    if 'title' in enhanced_data:
                                        enhanced_title = enhanced_data['title']
                                    if 'description' in enhanced_data:
                                        enhanced_description = enhanced_data['description']
                            except Exception as e:
                                print(f"Error parsing AI enhancement: {e}")
                    
                    # Build complete issue body with both enhanced content and original message
                    complete_body = enhanced_description + "\n\n"
                    
                    # Always include the original message for reference
                    complete_body += f"## ORIGINAL MESSAGE\n\n"
                    if referenced_message:
                        complete_body += f"From: {referenced_message.author.display_name}\n"
                        complete_body += f"Content: {referenced_message.content}\n\n"
                        
                        # Include related messages if any were found
                        if len(related_messages) > 1:  # If we have more than just the referenced message
                            complete_body += f"## RELATED MESSAGES\n\n"
                            for i, msg in enumerate(related_messages):
                                if msg["content"] != original_content:  # Skip the main message since it's already included
                                    complete_body += f"[{msg['time']}]: {msg['content']}\n\n"
                    elif issue_body:
                        complete_body += f"{issue_body}\n\n"
                    
                    # Add footer with metadata
                    complete_body += f"---\n"
                    complete_body += f"Created via Discord by {message.author.display_name} on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    
                    # Create GitHub issue using API
                    url = f"https://api.github.com/repos/{github_repo}/issues"
                    headers = {
                        "Authorization": f"token {github_token}",
                        "Accept": "application/vnd.github.v3+json"
                    }
                    data = {
                        "title": enhanced_title,
                        "body": complete_body
                    }
                    
                    issue_response = requests.post(url, headers=headers, json=data)
                    
                    if issue_response.status_code == 201:
                        issue_data = issue_response.json()
                        await message.reply(f"✅ GitHub issue created: {issue_data['html_url']}", mention_author=False)
                    else:
                        error_msg = f"Failed to create issue: {issue_response.status_code} - {issue_response.text}"
                        print(error_msg)
                        await message.reply(f"❌ Error creating GitHub issue. Status code: {issue_response.status_code}", mention_author=False)
            except Exception as e:
                print(f"Error creating GitHub issue: {e}")
                await message.reply(f"❌ Error creating GitHub issue: {str(e)}", mention_author=False)
        
        else:  # OTHER or unknown command type
            # Use AI to generate a response for unrecognized commands
            await message.reply(f"I'm not sure how to '{command_type}'. Could you be more specific?", mention_author=False)
    
    except discord.Forbidden:
        await message.reply("I don't have permission to do that.", mention_author=False)
    except Exception as e:
        print(f"Error executing command: {e}")
        await message.reply("I encountered an error while executing that command.", mention_author=False)

@bot.tree.command(name="setcommandaccess", description="Set which roles can use Sol natural language commands")
@app_commands.describe(
    role="The role that can use '.sol' commands",
    action="Whether to add or remove the role from authorized list"
)
async def set_command_access(
    interaction: discord.Interaction, 
    role: discord.Role,
    action: typing.Literal["add", "remove"]
):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("You need admin permissions to use this command", ephemeral=True)
        return
    
    # Initialize command_roles list if it doesn't exist
    if 'command_roles' not in BOT_CONFIG:
        BOT_CONFIG['command_roles'] = []
    
    # Get current allowed roles
    command_roles = BOT_CONFIG['command_roles']
    
    if action == "add":
        # Check if role is already in the list
        if role.id in command_roles:
            await interaction.response.send_message(f"Role '{role.name}' already has permission to use Sol commands", ephemeral=True)
            return
        
        # Add role to command access list
        command_roles.append(role.id)
        BOT_CONFIG['command_roles'] = command_roles
        save_config()
        
        await interaction.response.send_message(f"Role '{role.name}' can now use Sol commands", ephemeral=True)
    else:
        # Check if role is in the list
        if role.id not in command_roles:
            await interaction.response.send_message(f"Role '{role.name}' is not in the command access list", ephemeral=True)
            return
        
        # Remove role from command access list
        command_roles.remove(role.id)
        BOT_CONFIG['command_roles'] = command_roles
        save_config()
        
        await interaction.response.send_message(f"Role '{role.name}' can no longer use Sol commands", ephemeral=True)

@bot.tree.command(name="listcommandaccess", description="List roles that can use Sol natural language commands")
async def list_command_access(interaction: discord.Interaction):
    # Check if user is admin
    if not is_admin(interaction):
        await interaction.response.send_message("You need admin permissions to use this command", ephemeral=True)
        return
    
    # Get current allowed roles
    command_roles = BOT_CONFIG.get('command_roles', [])
    
    if not command_roles:
        await interaction.response.send_message("No roles have been specifically allowed to use Sol commands. Only users with manage_messages or administrator permissions can use them.", ephemeral=True)
        return
    
    # Get role names from IDs
    role_names = []
    for role_id in command_roles:
        role = interaction.guild.get_role(role_id)
        if role:
            role_names.append(f"• {role.name}")
    
    # Create embed
    embed = discord.Embed(
        title="Roles That Can Use Sol Commands",
        description="\n".join(role_names) if role_names else "No valid roles found",
        color=discord.Color.blue()
    )
    
    # Add note about admin permissions
    embed.add_field(name="Note", value="Users with manage_messages or administrator permissions can always use Sol commands regardless of roles", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
