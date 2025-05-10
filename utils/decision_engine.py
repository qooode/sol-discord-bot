"""
Decision engine for autonomous behavior.
Makes Sol think and decide for himself when and how to respond.
"""
import random
import time
import json
import requests
import os
import asyncio

class DecisionEngine:
    def __init__(self, api_key=None):
        """Initialize the decision engine"""
        # Get API key from environment if not provided
        self.api_key = api_key or os.getenv('OPENROUTER_API_KEY')
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        # Track decision history
        self.recent_decisions = {}
        # Track active conversations between users
        self.user_conversations = {}
        # Track conversation topics
        self.active_topics = {}
        # Count messages since Sol last spoke in each channel
        self.messages_since_last_response = {}
        
    def should_respond(self, message_content, message=None, user_id=None, channel_id=None, reply_to_message_id=None):
        """
        Decide if the bot should respond to a message
        
        Args:
            message_content: The content of the message
            message: The full Discord message object (optional)
            user_id: Discord user ID who sent the message
            channel_id: Discord channel ID where message was sent
            reply_to_message_id: ID of message this is replying to (if any)
            
        Returns:
            tuple: (bool, str) - whether to respond and reason
        """
        # Don't respond to empty messages
        if not message_content or not message_content.strip():
            return (False, "Empty message")
        
        # First check if this user was recently warned about rule violations
        # If so, be extremely careful about casual conversations
        if hasattr(self, 'recent_warnings') and user_id in self.recent_warnings:
            warning_data = self.recent_warnings[user_id]
            warning_time = warning_data['timestamp']
            warning_duration = warning_data['duration']
            warning_rule = warning_data.get('rule', 'rule violation')
            
            # Check if warning is still active
            if time.time() - warning_time < warning_duration:
                # If user was recently warned, only respond if:
                # 1. They're directly asking the bot a question
                # 2. They're apologizing or asking about the warning
                # 3. It's a completely different topic than what they were warned about
                
                # These checks are done in the bot's on_message handler
                # But also add specific logic to avoid engaging with sensitive topics
                lowered = message_content.lower()
                
                # Look for potential warning-related queries
                about_warning = any(term in lowered for term in ['warning', 'rule', 'sorry', 'apologize', 'timeout', 'mute'])
                
                # Look for attempts to continue problematic conversation
                if not about_warning and any(
                    related_term in lowered for related_term in warning_rule.lower().split()[:3]
                ):
                    print(f"Avoiding response to recently warned user discussing similar topic")
                    return (False, "User was recently warned about similar topic")
        
        # Find mentions of users in the message
        user_mentions = []
        if message and message.mentions:
            user_mentions = [user.id for user in message.mentions]
            
        # Check if a specific user is mentioned/addressed by @ or name
        message_targets_user = False
        target_user_name = None
        
        # First, check if this is a reply to another message
        is_replying_to_someone = False
        replying_to_bot = False
        reply_target_name = None
        
        if message and message.reference and message.reference.message_id:
            is_replying_to_someone = True
            
            # Try to fetch the message being replied to
            try:
                replied_message = None
                # Important: Since we're in a synchronous context but need to run an async function,
                # we need to handle this differently. The proper way is to pass the reference to the bot
                # and let the bot handle the async fetch in the on_message event.
                # For now, we'll just mark the flag that we're replying to someone
                # and handle the actual fetch in the bot.py file
                
                # Record that we've detected a reply, the actual fetch will happen in the bot's async context
                print(f"Detected reply to message ID: {message.reference.message_id}")
            except Exception as e:
                print(f"Error checking reply details: {e}")
        
        # Extract all user @mentions
        if message and hasattr(message, 'guild') and message.guild:
            # Get bot's own ID from the message or config
            bot_id = None
            if hasattr(message, 'guild') and message.guild and message.guild.me:
                bot_id = message.guild.me.id
            
            # Check for explicit @mentions of users
            for member in message.guild.members:
                # Check if this user is mentioned and is not the bot
                if member.id in user_mentions and (bot_id is None or member.id != bot_id):
                    message_targets_user = True
                    target_user_name = member.display_name
                    break
                    
            # Check for user names directly (without @)
            if not message_targets_user:
                for member in message.guild.members:
                    # Skip the bot itself
                    if bot_id and member.id == bot_id:
                        continue
                        
                    # Check if message starts with or addresses the user
                    member_name = member.display_name.lower()
                    content_lower = message_content.lower()
                    
                    # Detect if message starts with name or has "@name" format
                    if (content_lower.startswith(member_name + " ") or 
                        content_lower.startswith(member_name + ",") or
                        f"@{member_name}" in content_lower or
                        f", {member_name}" in content_lower or
                        f"hey {member_name}" in content_lower or
                        f"hi {member_name}" in content_lower):
                        
                        message_targets_user = True
                        target_user_name = member.display_name
                        break
        
        # Check if this is likely addressed to the bot
        bot_mentioned = False
        
        # Get the bot's name from config if possible
        bot_name = "sol"  # Default fallback name
        try:
            from config import BOT_CONFIG
            if 'name' in BOT_CONFIG:
                bot_name = BOT_CONFIG['name'].lower()
        except ImportError:
            pass
            
        # Dynamic mention pattern with bot's ID if available
        mention_pattern = f"<@{bot_id}>" if bot_id else None
            
        if (f"@{bot_name}" in message_content.lower() or 
            (mention_pattern and mention_pattern in message_content.lower()) or
            message_content.lower().startswith(f"{bot_name} ") or
            message_content.lower().startswith(f"{bot_name},")):
            bot_mentioned = True
        
        content_lower = message_content.lower()
        
        # IMPROVEMENT: Better detect follow-up questions to Sol's responses
        # Check if this is a very short message that could be a follow-up
        is_short_followup = False
        is_question = "?" in content_lower
        
        if len(content_lower.split()) <= 5 and channel_id:
            # Short messages that are questions are likely follow-ups
            if is_question:
                # Import message tracker if available to check if last message was from Sol
                try:
                    from bot import message_tracker
                    recent_messages = message_tracker.get_recent_channel_messages(channel_id, 3)
                    
                    if recent_messages and len(recent_messages) >= 1:
                        # Check if most recent message (other than current one) was from Sol
                        for recent_msg in recent_messages:
                            # If user_id is a string, convert bot_id to string for comparison
                            bot_id_str = str(bot_id) if bot_id else None
                            if bot_id_str and 'user_id' in recent_msg and recent_msg['user_id'] == bot_id_str:
                                print(f"Detected likely follow-up to Sol's message: '{message_content}'")
                                is_short_followup = True
                except ImportError:
                    # Message tracker not available, continue with normal checks
                    pass
        
        # If message is replying to someone else (not the bot) and doesn't mention the bot, don't respond
        if is_replying_to_someone and not replying_to_bot and not bot_mentioned:
            if reply_target_name:
                return (False, f"Message is replying to {reply_target_name}, not to Sol")
            else:
                return (False, "Message is replying to someone else, not to Sol")
        
        # If message targets another user and NOT Sol, don't respond
        if message_targets_user and not bot_mentioned:
            # Save this user conversation for future context
            if channel_id and user_id and target_user_name:
                convo_key = f"{channel_id}-{user_id}-{target_user_name}"
                self.user_conversations[convo_key] = time.time()
                print(f"Detected conversation between users: {user_id} talking to {target_user_name}")
                
            return (False, f"Message appears to be addressed to {target_user_name}, not Sol")
        
        # If this is a reply to the bot, we should probably respond
        if replying_to_bot:
            return (True, "Message is a reply to Sol's previous message")
            
        # If this is a likely follow-up question to Sol's message, respond
        if is_short_followup:
            return (True, "Short follow-up question to Sol's previous message")
            
        # Enhanced detection for follow-up questions about something Sol mentioned
        # Check if this appears to be asking about something Sol previously said
        if channel_id and "?" in content_lower and len(content_lower.split()) <= 15:
            try:
                from bot import message_tracker
                recent_messages = message_tracker.get_recent_channel_messages(channel_id, 10)
                
                # Look for words in the user's question that Sol mentioned in previous messages
                user_question_words = set([word.lower() for word in content_lower.split() if len(word) > 3])
                
                sol_previous_msgs = []
                for recent_msg in recent_messages:
                    # If user_id is a string, convert bot_id to string for comparison
                    bot_id_str = str(bot_id) if bot_id else None
                    if bot_id_str and 'user_id' in recent_msg and recent_msg['user_id'] == bot_id_str:
                        sol_previous_msgs.append(recent_msg.get('content', '').lower())
                
                # Look for overlap between Sol's recent messages and the user question
                for sol_msg in sol_previous_msgs:
                    sol_words = set([word.lower() for word in sol_msg.split() if len(word) > 3])
                    overlap = user_question_words.intersection(sol_words)
                    
                    # If there's significant overlap, this is likely about something Sol said
                    if len(overlap) >= 1 and len(user_question_words) >= 1:
                        print(f"Detected follow-up about something Sol mentioned. Overlapping terms: {overlap}")
                        return (True, "Question about something Sol previously mentioned")
            except (ImportError, Exception) as e:
                # Message tracker not available or error occurred, continue with normal checks
                print(f"Error checking for follow-up question: {e}")
        
        # No more automatic yes cases! ALL decisions go through AI
        # Just save this information for the AI to use
        contains_question = "?" in content_lower
        
        # Log factors that will influence the AI decision
        if bot_mentioned:
            print("FACTOR: Direct mention of bot (AI will decide)")
            
        if contains_question:
            print("FACTOR: Contains question (AI will decide)")
        
        # ALWAYS use AI to make the decision, no automatic responses
        if self.api_key:
            try:
                # Get channel context from message tracker
                channel_context = []
                if hasattr(message, 'channel') and message.channel:
                    # Import here to avoid circular import
                    from bot import message_tracker
                    if message.channel.id and message.author.id:
                        # Always use extended context to check for duplicates and conversation flow
                        channel_context = message_tracker.get_context(message.author.id, message.channel.id, extended=True)
                
                # Pass the FULL channel context for better decision making
                should_respond, reason = self._ask_ai(message_content, channel_context)
                if should_respond:
                    print(f"AI SAYS RESPOND: {reason}")
                    return True, reason
                else:
                    print(f"AI SAYS DON'T RESPOND: {reason}")
                    return False, reason
            except Exception as e:
                print(f"Error using AI decision: {e}")
                # If AI fails, default to responding
                return True, "ai_error_default_response"
        else:
            # If no API key, always respond
            return True, "no_api_key_default_response"

        
    def _ask_ai(self, message_content, context=None):
        """
        Ask AI for a decision about responding to a message
        
        Args:
            message_content: The content of the message
            context: Optional additional context
            
        Returns:
            tuple: (bool, str) - whether to respond and reason
        """
        from config import BOT_CONFIG
        
        # Get personality settings
        personality = BOT_CONFIG.get('ai_personality', {})
        chatty_level = personality.get('chatty', 0.5)  # Default 0.5 (medium chattiness)
        formality_level = personality.get('formality', 5)  # Default 5 (medium formality)
        
        # Use more message history for better conversation flow understanding
        simple_context = []
        
        # Use more messages if available to understand the full conversation
        messages_to_include = context[-20:] if len(context) > 20 else context
        
        for msg in messages_to_include:
            role = "user" if msg["role"] == "user" else "assistant"
            # Add full message content for better context understanding
            simple_context.append(f"{role}: {msg['content']}")
        
        context_str = "\n".join(simple_context)
        
        # Convert chatty_level to description
        chatty_desc = "very talkative and eager to join conversations"
        if chatty_level < 0.3:
            chatty_desc = "reserved and only responds when clearly addressed or asked a direct question"
        elif chatty_level < 0.6:
            chatty_desc = "moderately social and sometimes joins conversations that seem interesting"
        else: # chatty_level >= 0.6
            chatty_desc = "very chatty and eager to participate in most conversations, expressing itself with natural, varied casual language rather than repetitive slang."
            
        # Create decision prompt with personality parameters
        decision_prompt = f"""
        Analyze this message to determine if a response is appropriate. You need to decide if responding would be valuable.
        
        RESPONSE PROFILE:
        - Responsiveness: {chatty_desc}
        - Response probability: {chatty_level:.1f} on a 0.0-1.0 scale
        - Target response rate: approximately {int(chatty_level * 100)}% of messages
        
        CONVERSATION FLOW ANALYSIS:
        - CRITICALLY IMPORTANT: Check if another user has ALREADY answered the question
        - DO NOT repeat information that others have already provided
        - CAREFULLY ANALYZE who is talking to whom in the conversation
        - DO NOT respond to messages clearly directed at other users
        - IMPORTANT: Pay close attention to message context, thread replies, and username references
        - BUT if someone follows up on YOUR previous statement with a question, ALWAYS respond
        - Specifically, when someone asks about something you mentioned, ALWAYS answer them
        - If someone says "what is that" after you mentioned something, ALWAYS explain it
        
        DECISION CRITERIA:
        - RESPECT CONVERSATION CONTINUITY: If someone is responding directly to what YOU said, RESPOND
        - When a user asks about something you just mentioned, consider it directed at you even without your name
        - Recognize follow-up questions like "what's that?" or "what do you mean?" as directed at you if they come after your message
        - CRITICAL NEW RULE: DO NOT respond to messages that are clearly replying to other users in a thread
        - Be EXTREMELY CAREFUL about responding to messages that appear to be part of an ongoing conversation between other users
        - Check who people are referring to and replying to in messages to avoid intruding on others' conversations
        - DO NOT invent information or facts you don't know with 100% certainty
        - For casual topics where internet search can help (like movie release dates, sports scores, etc) - RESPOND
        - STRONG BIAS TOWARD NOT RESPONDING in conversations clearly between other users
        - NEVER respond just to say you agree with what another user already said
        - If another user has answered a question correctly, DO NOT respond at all
        - For entertainment/media questions you're not 100% sure about, RESPOND (you can search the internet)
        
        RECENT CONVERSATION HISTORY:
        {context_str}
        
        LAST USER MESSAGE:
        {message_content}
        
        Determine if you should respond to this message based on the conversation context.
        Respond with a JSON object containing two fields:
        {{"should_respond": true/false, "reason": "brief explanation of decision"}}
        """
        
        try:
            # Prepare API call - use Anthropic Claude instead of OpenAI GPT for better context understanding
            payload = {
                "model": "google/gemini-2.5-flash-preview",
                "messages": [{
                    "role": "user",
                    "content": decision_prompt
                }]
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://discord-bot.example.com",
                "X-Title": "Discord AI Assistant"
            }
            
            # Make API request
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=5  # 5 second timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_response = data["choices"][0]["message"]["content"]
                    
                    # Extract JSON
                    try:
                        # Find JSON object
                        start = ai_response.find('{')
                        end = ai_response.rfind('}') + 1
                        
                        if start != -1 and end != -1:
                            json_str = ai_response[start:end]
                            decision = json.loads(json_str)
                            return decision.get("should_respond", False), decision.get("reason", "Unknown reason")
                    except Exception as e:
                        print(f"Error parsing AI response JSON: {e}")
                        # Try to extract decision more simply
                        if "should_respond" in ai_response.lower() and "true" in ai_response.lower():
                            return True, "Simple text extraction: should respond"
                        elif "should_respond" in ai_response.lower() and "false" in ai_response.lower():
                            return False, "Simple text extraction: should not respond"
            
            # Default to conservative response - don't respond if we can't decide
            return False, "Failed to get clear AI decision, defaulting to not responding"
            
        except Exception as e:
            print(f"Error in AI decision: {e}")
            # Default to not responding if there's an error
            return False, f"Error in AI decision: {str(e)}"
        
    def needs_internet_search(self, message_content, context):
        """
        Determine if a message needs internet search to answer properly
        
        Args:
            message_content: The content of the message
            context: Additional context (conversation history)
            
        Returns:
            bool: Whether internet search is needed
        """
        if not message_content:
            return False
            
        message_lower = message_content.lower()
        
        # Check if this is a follow-up question to something Sol already said
        is_follow_up = False
        recent_bot_message = ""
        user_question = message_content.lower()
        
        # Look for short follow-up questions like "who?", "what?", etc.
        follow_up_indicators = ["who", "what", "which", "when", "how", "why", "where", "tell me more", "explain", "elaborate", "details", "specifically"]
        is_short_question = any(user_question.strip().startswith(word) for word in follow_up_indicators) or len(user_question.split()) < 5
        
        # Check for context (previous bot message)
        if context and len(context) >= 2:
            for i in range(len(context)-1, 0, -1):
                if context[i]['role'] == 'assistant':
                    recent_bot_message = context[i]['content']
                    if i+1 < len(context) and context[i+1]['role'] == 'user' and context[i+1]['content'].lower() == message_content.lower():
                        is_follow_up = True
                    break
        
        # If this looks like a follow-up to something Sol said
        if is_short_question and recent_bot_message:
            # Use AI to decide if we need to search the internet
            try:
                prompt = f"""
                You need to analyze if a follow-up question requires internet search to provide specific factual information.
                
                RECENT CONVERSATION:
                Sol: {recent_bot_message}
                User: {message_content}
                
                Based on this exchange:
                1. Is the user asking for specific details that weren't provided in Sol's message?
                2. Would internet search significantly improve the quality of the response?
                3. Is the user asking for clarification on something factual (rather than opinion)?
                4. Does Sol's previous response hint at knowledge that would require internet search to elaborate on?
                5. Is this about a specific event, person, or topic that requires factual verification?
                
                Respond with ONLY a single JSON object with this structure:
                {{"needs_search": true/false, "reason": "brief explanation"}}
                
                Only return true if internet search would SIGNIFICANTLY improve the response with factual information.
                """
                
                # Just reuse existing API key and URL
                from config import BOT_CONFIG
                from utils.ai_handler import AIHandler
                
                # Use AI to decide if we need internet search
                api_key = BOT_CONFIG.get('openrouter_api_key') or os.getenv('OPENROUTER_API_KEY')
                ai_handler = AIHandler(api_key)
                
                payload = {
                    "model": "google/gemini-2.5-flash-preview",
                    "messages": [{
                        "role": "user",
                        "content": prompt
                    }],
                    "max_tokens": 150
                }
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=3
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        ai_response = data["choices"][0]["message"]["content"]
                        
                        # Extract JSON
                        json_start = ai_response.find('{')
                        json_end = ai_response.rfind('}')
                        
                        if json_start != -1 and json_end != -1:
                            json_str = ai_response[json_start:json_end+1]
                            decision = json.loads(json_str)
                            
                            needs_search = decision.get("needs_search", False)
                            reason = decision.get("reason", "No reason provided")
                            
                            print(f"Follow-up analysis: needs_search={needs_search} - {reason}")
                            return needs_search
            except Exception as e:
                print(f"Error in follow-up analysis: {e}")
        
        # If not a follow-up, check for other Internet search indicators
        needs_search = self._check_general_search_indicators(message_content)
        return needs_search
        
    def check_moderation(self, message_content, user_id=None, channel_id=None, member=None):
        """
        Check if a message violates community rules using Gemini 2.5 model
        
        Args:
            message_content: Content of the message to check
            user_id: The user ID who sent the message (optional)
            channel_id: The channel ID where the message was sent (optional)
            member: The Discord member object (optional, used for role checking)
            
        Returns:
            tuple: (violates_rules, rule_violated, explanation, alternative_suggestion)
        """
        if not message_content or not message_content.strip():
            return (False, None, None, None)
            
        # Get moderation settings from config
        from config import BOT_CONFIG

        # Check if moderation is enabled
        if not BOT_CONFIG.get('moderation', {}).get('enabled', True):
            return (False, None, None, None)
        
        # Check if user has exempt roles
        if member and hasattr(member, 'roles'):
            exempt_roles = BOT_CONFIG.get('moderation', {}).get('exempt_roles', [])
            
            # If exempt_roles contains role IDs, check if user has any of those roles
            if exempt_roles and any(role.id in exempt_roles for role in member.roles):
                print(f"User {user_id} exempt from moderation due to role")
                return (False, None, None, None)
                
            # If exempt_roles contains role names, check if user has any of those roles
            if exempt_roles and any(role.name.lower() in [r.lower() for r in exempt_roles if isinstance(r, str)] for role in member.roles):
                print(f"User {user_id} exempt from moderation due to role")
                return (False, None, None, None)
        
        # Ignore very short messages (1-2 words) unless AI flags them
        is_short_message = len(message_content.split()) <= 2 and len(message_content) < 15
            
        try:
            # Get rules from config
            rules = BOT_CONFIG.get('moderation', {}).get('rules', [
                "No hate speech or bullying. Treat all members with respect.",
                "No trash-talking competitors or non-constructive comparisons.",
                "No third-party addon/mod discussion.",
                "Respect intellectual property laws.",
                "No inappropriate language or excessive rudeness."
            ])
            
            # Format rules for the AI
            rules_text = ""
            for i, rule in enumerate(rules, 1):
                rules_text += f"{i}. {rule}\n"
            
            # Create the prompt for rule violation check, enhanced for context-awareness
            prompt = f"""
            Your job is to determine if a message violates community rules, with a focus on context and intent.
            Be very discerning about WHEN to flag violations - don't disrupt normal conversation.
            
            COMMUNITY RULES:
            {rules_text}
            
            MESSAGE TO REVIEW:
            "{message_content}"
            
            SERIOUS VIOLATIONS TO WATCH FOR:
            1. Discussions that actively promote jailbreaking devices/software
            2. Sharing methods for piracy or copyright infringement
            3. Instructions on bypassing terms of service
            4. Hate speech or harassment targeted at specific people
            5. Content that could put the entire community at risk
            6. Extreme claims that could violate platform policies
            
            CONTEXTUAL JUDGMENT GUIDELINES:
            1. Focus on INTENT rather than just keywords - distinguish between discussing a topic vs actively promoting violations
            2. Allow general discussions about features, services, or technologies when not promoting rule violations
            3. Allow factual discussions or questions about topics, including addons, software modifications, or device features, even if they touch on controversial subjects. Do not flag users merely asking *about* such topics (e.g., "Does an addon for X exist?", "How does Y feature work?"), especially if their inquiry is general and does not explicitly request, provide, or promote specific methods for piracy, TOS violations, or access to unofficial/illegal sources. Focus on whether the message *itself* shares or solicits rule-breaking content/instructions, rather than just mentioning a potentially sensitive topic in a question.
            4. Only flag content if it ACTIVELY encourages or instructs others to violate rules
            5. Consider if the message provides specific actionable instructions for violating TOS or rules
            6. Allow casual profanity that isn't directed at others
            7. For very short messages (1-2 words), be extremely lenient unless truly harmful
            8. Prioritize conversation flow - don't interrupt for minor issues
            9. For conspiracy theories and misinformation, flag but suggest providing factual context
            10. For requests about illegal/TOS-breaking activities, flag but suggest legal alternatives
            11. Distinguish between users stating they *found*, are *looking for*, or are *in the process of acquiring/downloading* content (which may be ambiguous regarding the source or method) versus users *explicitly sharing links/methods* to unofficial sources or *actively encouraging others* to use them. Do not flag mere mentions of having, seeking, or downloading content (regardless of described speed or progress) unless accompanied by clear promotion of unofficial sources or detailed instructions for piracy/TOS violation.
            
            SPECIFIC EXAMPLES OF WHAT'S ALLOWED:
            - General discussions about disliking ads or features
            - Questions about why certain things work the way they do
            - Discussions about official/approved/legal methods for customization
            - Theoretical discussions about technology
            - Mentioning topics without actively encouraging their use
            - Discussing that something exists WITHOUT providing instructions for rule violations
            
            EXAMPLES OF WHAT'S NOT ALLOWED:
            - Providing step-by-step instructions for circumventing TOS
            - Posting links to tools explicitly designed for rule violations
            - Actively encouraging others to violate rules
            - Detailed guides on how to infringe copyright
            - Harassing or targeting specific individuals
            
            SPECIAL HANDLING INSTRUCTIONS:
            1. For conspiracy theories or misinformation: Flag, but suggest providing factual context
            2. For requests about illegal/TOS activities: Flag, but suggest legal alternatives
               - For ad complaints: Suggest premium subscriptions or legitimate ad-blockers
               - For content access: Suggest official channels or legal alternatives
               - For device modification: Suggest official customization options
            
            IMPORTANT: Pay attention to INTENT and CONTEXT, not just the presence of certain words or topics.
            
            Analyze the message with these guidelines and respond in this JSON format:
            {{
              "violates_rules": true/false,
              "rule_violated": "Brief description of the rule violated (if any)",
              "explanation": "Brief explanation of why this violates rules (if applicable)",
              "severity": "low/medium/high",
              "alternative_suggestion": "Legal alternative to suggest (if applicable)"
            }}
            """
            
            # Simplified prompt for short messages
            if is_short_message:
                # For very short messages, use a simpler moderation check
                # that only catches obvious violations
                prompt = f"""
                Determine if this short message clearly violates community rules:
                
                MESSAGE: "{message_content}"
                
                COMMUNITY RULES:
                {rules_text}
                
                SERIOUS VIOLATIONS TO WATCH FOR:
                1. Discussions that actively promote jailbreaking devices/software
                2. Sharing methods for piracy or copyright infringement
                3. Instructions on bypassing terms of service
                4. Hate speech or harassment targeted at specific people
                5. Content that could put the entire community at risk
                6. Extreme claims that could violate platform policies
                
                For short messages like this, ONLY flag if it's an obvious rule violation.
                Don't flag ambiguous short messages - give the benefit of doubt.
                
                Respond with this JSON:
                {{
                  "violates_rules": true/false,
                  "rule_violated": "Brief description of the rule violated (if any)",
                  "explanation": "Brief explanation of why this violates rules (if applicable)",
                  "severity": "low/medium/high",
                  "alternative_suggestion": "Legal alternative to suggest (if applicable)"
                }}
                """
            
            # Send moderation check to GPT-4/Gemini
            # For performance and simplicity, we'll use the faster model here
            # even if the main chat uses GPT-4
            # Get a specific moderation model from config if set, otherwise use the default model
            model = "google/gemini-2.5-flash-preview"  # Default to Gemini for moderation
            
            try:
                # Make the API call directly with requests
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://discord-bot.example.com",
                    "X-Title": "Sol Discord Bot"
                }
                
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1  # Low temperature for more consistent moderation
                }
                
                # Make the API request
                response = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=10  # 10 second timeout
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Extract the JSON response from the model
                    if "choices" in result and len(result["choices"]) > 0:
                        json_str = result["choices"][0]["message"]["content"]
                        
                        # Find the JSON part (in case there's surrounding text)
                        json_start = json_str.find('{')
                        json_end = json_str.rfind('}')
                        
                        if json_start != -1 and json_end != -1:
                            json_str = json_str[json_start:json_end+1]
                            
                            # Parse the moderation result
                            moderation_result = json.loads(json_str)
                            
                            violates_rules = moderation_result.get("violates_rules", False)
                            rule_violated = moderation_result.get("rule_violated", None)
                            explanation = moderation_result.get("explanation", None)
                            severity = moderation_result.get("severity", "medium")
                            
                            # Track violations if user ID is provided
                            if violates_rules and user_id:
                                # Store violation in memory for tracking repeat offenses
                                if not hasattr(self, 'violation_history'):
                                    self.violation_history = {}
                                    
                                if user_id not in self.violation_history:
                                    self.violation_history[user_id] = []
                                    
                                self.violation_history[user_id].append({
                                    'timestamp': time.time(),
                                    'channel_id': channel_id,
                                    'rule_violated': rule_violated,
                                    'severity': severity
                                })
                                
                                # Limit history size
                                if len(self.violation_history[user_id]) > 10:
                                    self.violation_history[user_id] = self.violation_history[user_id][-10:]
                            
                            alternative_suggestion = moderation_result.get("alternative_suggestion", None)
                            
                            return (violates_rules, rule_violated, explanation, alternative_suggestion)
                
                # Default to no violation if we couldn't parse JSON properly
                return (False, None, None, None)
                
            except Exception as e:
                print(f"Error processing moderation check: {e}")
                return (False, None, None, None)
                
        except Exception as e:
            print(f"Error in moderation check: {e}")
            return (False, None, None, None)
            
    def get_violation_count(self, user_id, hours=24):
        """
        Get count of violations for a user within specified timeframe
        
        Args:
            user_id: Discord user ID
            hours: Timeframe to check in hours
            
        Returns:
            int: Number of violations
        """
        if not hasattr(self, 'violation_history') or user_id not in self.violation_history:
            return 0
            
        # Count violations within the timeframe
        cutoff_time = time.time() - (hours * 3600)
        recent_violations = [v for v in self.violation_history[user_id] if v['timestamp'] >= cutoff_time]
        
        return len(recent_violations)
    
    def _check_general_search_indicators(self, message_content):
        """Check for general indicators that a message might need internet search"""
        message_lower = message_content.lower()
        
        # Look for fact-based queries - balanced indicators that are neither too specific nor too general
        search_indicators = [
            # Time-related indicators
            "latest", "recent", "new", "current", "today", "yesterday", "week", "month", "year",
            "now", "soon", "upcoming", "schedule", "release date", "when", "history", "ago",
            
            # Media and entertainment indicators
            "movie", "show", "series", "game", "album", "song", "book", "release", "trailer",
            "episode", "season", "play", "stream", "watch", "listen", "read", "sequel", "prequel",
            
            # Technical indicators
            "spec", "version", "compatible", "support", "format", "codec", "standard", "protocol",
            "dv", "dolby", "hdr", "uhd", "resolution", "frame rate", "framerate", "fps", "khz",
            "bandwidth", "bitrate", "output", "input", "port", "device", "dongle", "adapter",
            
            # Demographic and statistical indicators
            "rate", "percentage", "average", "median", "population", "birth", "death", "growth",
            "trend", "increase", "decrease", "statistic", "demographic", "census", "survey",
            "europe", "american", "asian", "african", "global", "worldwide", "country", "region",
            
            # Product and business indicators
            "price", "cost", "worth", "available", "launch", "announce", "company", "business", 
            "manufacturer", "producer", "studio", "developer", "publisher", "brand", "model",
            
            # Internet and tech platforms
            "website", "app", "social media", "twitter", "facebook", "instagram", "tiktok",
            "post", "tweet", "video", "trending", "viral", "online", "offline", "download",
            
            # News and event indicators
            "news", "update", "event", "incident", "happen", "occur", "situation", "development",
            "announcement", "reveal", "unveil", "discover", "report", "state", "confirm", "deny",
            
            # Comparison indicators
            "difference", "better", "worse", "faster", "slower", "cheaper", "expensive", "versus",
            "compare", "alternative", "option", "recommendation", "review", "rating", "score"
        ]
        
        # Check for proper nouns (names of people, places, things)
        words = message_content.split()
        contains_proper_nouns = any(word[0].isupper() for word in words if len(word) > 1 and word not in ["I", "I'm"])
        
        # Look for search indicators
        contains_search_term = any(term in message_lower for term in search_indicators)
        
        # Enhanced detection for technical acronyms and abbreviations
        # Look for patterns like "DV8" or "HDR10+" or technical terms with numbers
        technical_pattern = any(word for word in words if (
            (len(word) >= 2 and word.isupper()) or  # All caps abbreviation
            (any(c.isupper() and c.isalpha() and any(d.isdigit() for d in word) for c in word))  # Mixed alpha-numeric with caps
        ))
        
        # Check for question structure that implies need for factual info
        is_question = "?" in message_content
        starts_with_question_word = any(message_lower.strip().startswith(q) for q in [
            "what", "how", "why", "when", "where", "who", "which", 
            "can", "could", "will", "would", "should", "is", "are", "explain"
        ])
        
        # Technical terms with numbers are almost always factual questions
        if technical_pattern and (is_question or starts_with_question_word):
            print("Detected technical term/abbreviation question - using internet search")
            return True
        
        # If this is clearly a factual question with specific entities, it likely needs search
        if (is_question and (contains_proper_nouns or contains_search_term)):
            print("Detected factual question with entities - using internet search")
            return True
            
        # If it's a question about a specific topic or region, search
        if starts_with_question_word and contains_search_term:
            print("Detected question about specific topic - using internet search")
            return True
        
        return False
        
    def determine_response_type(self, message_content, context):
        """
        Determine what type of response would be appropriate using AI
        
        Args:
            message_content: The content of the message
            context: List of previous messages
            
        Returns:
            str: Response type ('short_answer', 'detailed_answer', etc.)
        """
        print("Determining response type with AI...")
        
        # Simple heuristics for common cases to avoid API calls
        if '?' in message_content:
            return "answer"
            
        # Create simplified message list for context
        recent_msgs = context[-5:] if len(context) > 5 else context
        context_text = "\n".join([f"{msg['role']}: {msg['content'][:100]}..." if len(msg['content']) > 100 else f"{msg['role']}: {msg['content']}" for msg in recent_msgs])
        
        # Try to use AI to determine response type
        try:
            # Create prompt for response type
            prompt = f"""
            Analyze this message and determine what type of response would be most appropriate.
            
            RECENT CONVERSATION:
            {context_text}
            
            CURRENT MESSAGE: {message_content}
            
            Choose ONE response type from these options:
            1. "greeting" - If the message is a greeting or introduction
            2. "answer" - If the message is a question needing information
            3. "helpful" - If the message is asking for help or assistance
            4. "opinion" - If the message is asking for thoughts or discussion
            5. "empathetic" - If the message has emotional content needing empathy
            6. "casual" - For general conversation that doesn't fit other categories
            
            Respond with just the response type in quotes (e.g. "casual"), no explanation.
            """
            
            # Make API request
            payload = {
                "model": "google/gemini-2.5-flash-preview",
                "messages": [{
                    "role": "user",
                    "content": prompt
                }]
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://discord-bot.example.com",
                "X-Title": "Sol Response Type Decision"
            }
            
            # Very short timeout - if AI is slow, just use fallback
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=2)
            response_json = response.json()
            
            if 'choices' in response_json and len(response_json['choices']) > 0:
                ai_response = response_json['choices'][0]['message']['content'].strip().lower()
                
                # Extract just the response type from quotes if needed
                if '"' in ai_response:
                    # Extract text in quotes
                    import re
                    match = re.search(r'"([^"]*)"', ai_response)
                    if match:
                        ai_response = match.group(1)
                
                # Validate response is one of our valid types
                valid_types = ["greeting", "answer", "helpful", "opinion", "empathetic", "casual"]
                if ai_response in valid_types:
                    print(f"AI determined response type: {ai_response}")
                    return ai_response
        
        except Exception as e:
            print(f"Error using AI for response type: {e}")
        
        # Fallback to casual if AI failed
        print("Falling back to casual response type")
        return "casual"
        
    def decide_response_length(self, message_type, message_content):
        """
        Decide how lengthy the response should be based on message type
        
        Args:
            message_type: Type of response to generate
            message_content: Original message content
            
        Returns:
            str: "short", "medium", or "long"
        """
        from config import BOT_CONFIG
        
        # Get formality from personality settings
        personality = BOT_CONFIG.get('ai_personality', {})
        formality = personality.get('formality', 5)  # Default to middle formality
        
        # MUCH stronger bias toward short responses across the board
        # Higher formality = slightly longer responses but still keeping them short
        short_chance = 0.75 - (formality * 0.02)  # 0.65 to 0.85 based on formality (much higher chance of short)
        long_chance = 0.05 + (formality * 0.01)   # 0.0 to 0.15 based on formality (much lower chance of long)
        medium_chance = 1.0 - short_chance - long_chance
        
        # Message type specific adjustments
        if message_type == "answer":
            # Questions get slightly more detailed answers
            short_chance *= 0.8
            long_chance *= 1.2
        elif message_type == "greeting":
            # Greetings are very short
            short_chance = 0.95
            long_chance = 0.0
        elif message_type == "helpful":
            # Help responses can be more detailed
            short_chance *= 0.7
            long_chance *= 1.5
        elif message_type == "opinion":
            # Opinions can be slightly more detailed
            short_chance *= 0.8
            long_chance *= 1.2
        elif message_type == "casual":
            # Make casual responses VERY short - more like normal Discord chat
            short_chance = 0.9
            medium_chance = 0.09
            long_chance = 0.01
        elif message_type == "empathetic":
            # Empathetic responses should be brief but thoughtful
            short_chance = 0.8
            medium_chance = 0.18
            long_chance = 0.02
        
        # Special case: If the message we're responding to is short, our response should be short too
        if len(message_content.split()) <= 10:  # For very short messages
            short_chance += 0.2  # Dramatically increase chance of short response
            if short_chance > 1.0:
                short_chance = 0.95
                medium_chance = 0.05
                long_chance = 0.0
        
        # Normalize probabilities
        total = short_chance + medium_chance + long_chance
        if total > 1.0:
            short_chance /= total
            medium_chance /= total
            long_chance /= total
        
        # Roll for length
        roll = random.random()
        if roll < short_chance:
            return "short"
        elif roll < short_chance + medium_chance:
            return "medium"
        else:
            return "long"
    
    def should_wait_longer(self, message_content, is_burst):
        """
        Decide if Sol should wait longer before responding
        
        Args:
            message_content: Message content
            is_burst: Whether this is part of a message burst
            
        Returns:
            bool: True if Sol should wait longer
        """
        # For AI-managed patience, we NEVER need to wait longer here
        # This completely disables the old waiting logic to prevent conflicts
        # with the AI-powered patience decisions in message_tracker
        return False
        
        '''
        # This code is all disabled now - AI handles waiting decisions
        
        # If it's a message burst, we should wait
        if is_burst:
            return True
            
        # If message is very short, it might be incomplete
        if len(message_content) < 10:
            return True
            
        # If message ends with ... or similar, might be incomplete
        if message_content.endswith(('...', '..', '.', ',')):
            return True
            
        # If asking complex question, wait for clarification
        if self._is_question(message_content) and len(message_content) < 20:
            return True
        '''
            
        # Always respond immediately - AI patience handles waiting
