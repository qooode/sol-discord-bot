"""
Utility to track and manage message history for conversation context.
Tracks messages per user with time awareness.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import time
import os
import json
import requests

class MessageTracker:
    def __init__(self, context_window=10, max_age_hours=12):
        """
        Initialize the message tracker
        
        Args:
            context_window (int): Number of messages to remember per user per channel
            max_age_hours (int): Maximum age of messages to consider relevant (0 for no limit)
        """
        # Structure: {(user_id, channel_id): [messages]}
        self.messages = defaultdict(list)
        # Track last message timestamp per user per channel
        self.last_message_time = {}
        # Track when a user started a message burst
        self.message_burst_start = {}
        self.context_window = context_window
        self.max_age_hours = max_age_hours
        # OpenRouter API key for AI patience decisions
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        # Default patience level (0-10 scale)
        self.patience_level = 5
        
    def add_message(self, user_id, content, channel_id):
        """
        Add a message to the tracker
        
        Args:
            user_id: Discord user ID
            content: Message content
            channel_id: Discord channel ID
            
        Returns:
            bool: True if this is part of a message burst (rapid messages from same user)
        """
        key = (user_id, channel_id)
        timestamp = time.time()
        
        # Track message burst - if user sent message in last 15 seconds, consider it part of same thought
        is_burst = False
        if key in self.last_message_time:
            time_since_last = timestamp - self.last_message_time[key]
            if time_since_last < 15:  # 15 seconds threshold for message burst
                is_burst = True
                # Keep track of when this burst started
                if key not in self.message_burst_start:
                    self.message_burst_start[key] = self.last_message_time[key]
            else:
                # Reset burst start time if it's been too long
                if key in self.message_burst_start:
                    del self.message_burst_start[key]
        
        # Update last message time
        self.last_message_time[key] = timestamp
        
        # Add message to history
        self.messages[key].append({
            'timestamp': timestamp,
            'content': content,
            'role': 'user',
            'user_id': str(user_id)  # Store user_id as string for consistent comparisons
        })
        
        # Prune old messages if max age is set
        if self.max_age_hours > 0:
            self.prune_user_messages(user_id, channel_id)
        
        # Trim to context window
        if len(self.messages[key]) > self.context_window:
            self.messages[key] = self.messages[key][-self.context_window:]
            
        return is_burst
    
    def add_bot_response(self, user_id, content, channel_id):
        """
        Add a bot's response to the conversation
        
        Args:
            user_id: Discord user ID the bot is responding to
            content: Bot's response content
            channel_id: Discord channel ID
        """
        key = (user_id, channel_id)
        timestamp = time.time()
        
        self.messages[key].append({
            'timestamp': timestamp,
            'content': content,
            'role': 'assistant',
            'user_id': 'bot'  # Add consistent user_id for bot messages
        })
        
        # Trim to context window
        if len(self.messages[key]) > self.context_window:
            self.messages[key] = self.messages[key][-self.context_window:]
    
    def get_context(self, user_id, channel_id, extended=False):
        """
        Get the conversation context for a user in a channel
        
        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            extended: If True, retrieve up to 100 messages for deeper context
            
        Returns:
            list: List of message dicts with role and content
        """
        key = (user_id, channel_id)
        
        # Prune old messages if max age is set
        if self.max_age_hours > 0:
            self.prune_user_messages(user_id, channel_id)
        
        # Get messages (all if extended, or just the context window)
        messages_to_include = self.messages[key]
        
        # If extended context is requested, include more messages (up to 100)
        if not extended:
            # Use standard context window
            messages_to_include = messages_to_include[-self.context_window:] if len(messages_to_include) > self.context_window else messages_to_include
        else:
            # Use extended context (up to 100 messages)
            max_extended = 100
            messages_to_include = messages_to_include[-max_extended:] if len(messages_to_include) > max_extended else messages_to_include
            print(f"Using extended context with {len(messages_to_include)} messages")
        
        # Convert to format expected by AI models
        return [
            {'role': msg['role'], 'content': msg['content']} 
            for msg in messages_to_include
        ]
    
    def clear_history(self, user_id, channel_id):
        """
        Clear conversation history for a user in a channel
        
        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
        """
        key = (user_id, channel_id)
        if key in self.messages:
            self.messages[key] = []
            
    def prune_user_messages(self, user_id, channel_id):
        """
        Remove old messages for a specific user in a channel
        
        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
        """
        if self.max_age_hours <= 0:
            return
            
        key = (user_id, channel_id)
        current_time = time.time()
        max_age_seconds = self.max_age_hours * 3600
        
        self.messages[key] = [
            msg for msg in self.messages[key]
            if current_time - msg['timestamp'] < max_age_seconds
        ]
        
    def is_thinking(self, user_id, channel_id):
        """
        Check if a user is likely still formulating their thought (in a message burst)
        
        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            
        Returns:
            bool: True if the user seems to be in the middle of typing multiple messages
        """
        # NEVER assume user is thinking - be super responsive
        return False
        
        # Old code below - all disabled now
        '''
        key = (user_id, channel_id)
        
        # If we haven't seen a message from this user, they're not thinking
        if key not in self.last_message_time:
            return False
            
        # If there's an ongoing message burst
        if key in self.message_burst_start:
            # How long ago the burst started
            burst_duration = time.time() - self.message_burst_start[key]
            # If burst is less than 45 seconds old, they might still be typing
            return burst_duration < 45
        '''
            
        return False
        
    def should_wait_for_more_context(self, user_id, channel_id):
        """
        Determine if we should wait for more context before responding
        
        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            
        Returns:
            bool: True if we should wait for more messages
        """
        key = (user_id, channel_id)
        
        # If we don't have any messages or don't have an API key, don't wait
        if key not in self.messages or not self.api_key:
            return False
            
        # Get the last few messages for context
        recent_messages = self.messages[key][-3:] if len(self.messages[key]) >= 3 else self.messages[key]
        
        # Use AI to decide if we should wait
        should_wait = self._ask_ai_about_patience(recent_messages)
        print(f"AI patience decision: should_wait={should_wait}")
        return should_wait
    
    def _ask_ai_about_patience(self, messages):
        """
        Ask AI if we should wait for more context from the user
        
        Args:
            messages: Recent messages from the user
            
        Returns:
            bool: True if we should wait, False if we should respond now
        """
        if not self.api_key or not messages:
            return False
            
        # Create simplified context for the patience prompt
        messages_text = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            content = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
            messages_text.append(f"{role}: {content}")
            
        # Get timestamps to calculate timing
        timestamps = [msg["timestamp"] for msg in messages if msg["role"] == "user"]
        timing_info = ""
        
        if len(timestamps) >= 2:
            avg_time_between = sum(timestamps[i] - timestamps[i-1] for i in range(1, len(timestamps))) / (len(timestamps) - 1)
            timing_info = f"\nAverage time between messages: {avg_time_between:.1f} seconds"
            
        last_message = messages[-1]["content"] if messages else ""
        time_since_last = time.time() - messages[-1]["timestamp"] if messages else 0
        
        # Get patience level from config or use default
        from config import BOT_CONFIG
        personality = BOT_CONFIG.get('ai_personality', {})
        patience_level = personality.get('patience', self.patience_level)
        
        # Convert patience level to description
        patience_desc = "moderately patient"
        if patience_level <= 3:
            patience_desc = "very impatient and eager to respond quickly"
        elif patience_level <= 7:
            patience_desc = "moderately patient and waits a reasonable time for complete thoughts"
        else:
            patience_desc = "extremely patient and always waits for complete thoughts before responding"
            
        # Create prompt for patience decision
        patience_prompt = f"""
        You are Sol, a Discord bot that needs to decide whether to wait for more messages or respond now.
        
        YOUR PATIENCE SETTINGS:
        - Your patience level is {patience_level}/10
        - You are {patience_desc}
        - The higher your patience, the more likely you should wait for complete thoughts
        
        RECENT CONVERSATION:
        {"\n".join(messages_text)}
        {timing_info}
        
        Time since last message: {time_since_last:.1f} seconds
        
        Analyze the last message and decide if the user is likely still typing or if their thought is incomplete.
        Consider:
        1. Is the message very short (less than 20 chars)?
        2. Does it end with ellipsis, comma, or no punctuation?
        3. Does it seem like an incomplete thought?
        4. Is it a conversation starter that might be followed by more details?
        5. Given your patience level ({patience_level}/10), should you wait?
        
        Respond with just a single JSON object containing one field:
        {{"wait": true}} if I should wait for more messages from the user
        {{"wait": false}} if I should respond immediately
        """
        
        try:
            # Prepare API call
            payload = {
                "model": "google/gemini-2.5-flash-preview",
                "messages": [{
                    "role": "user",
                    "content": patience_prompt
                }]
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://discord-bot.example.com",
                "X-Title": "Discord Patience Decision"
            }
            
            # Make API request - with very short timeout
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=1  # 1 second timeout - don't wait too long for patience decision
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_response = data["choices"][0]["message"]["content"]
                    
                    # Try to extract JSON
                    try:
                        # Find JSON object
                        start = ai_response.find('{')
                        end = ai_response.rfind('}')
                        
                        if start != -1 and end != -1:
                            json_str = ai_response[start:end+1]
                            decision = json.loads(json_str)
                            return decision.get("wait", False)
                    except:
                        # Default to not waiting if parsing fails
                        if "wait" in ai_response.lower() and "true" in ai_response.lower():
                            return True
                            
            # If anything goes wrong, don't wait
            return False
            
        except Exception as e:
            print(f"Error in patience decision: {e}")
            return False
        
    def get_recent_channel_messages(self, channel_id, count=10):
        """
        Get recent messages from a specific channel, regardless of user
        
        Args:
            channel_id: Discord channel ID
            count: Max number of messages to return
            
        Returns:
            list: Recent messages in the channel, newest first
        """
        # Collect all messages from this channel
        channel_messages = []
        for (user_id, chan_id), messages in self.messages.items():
            if chan_id == channel_id:
                channel_messages.extend(messages)
        
        # Sort by timestamp, newest first
        channel_messages.sort(key=lambda msg: msg.get('timestamp', 0), reverse=True)
        
        # Return most recent messages
        return channel_messages[:count]
    
    def prune_old_messages(self):
        """
        Remove messages older than the configured max age for all users
        """
        if self.max_age_hours <= 0:
            return
            
        current_time = time.time()
        max_age_seconds = self.max_age_hours * 3600
        
        for key in self.messages:
            self.messages[key] = [
                msg for msg in self.messages[key]
                if current_time - msg['timestamp'] < max_age_seconds
            ]

    def clear_old_messages(self):
        """
        Remove messages older than max_age_hours
        """
        if self.max_age_hours == 0:
            return  # No age limit
            
        cutoff_time = time.time() - (self.max_age_hours * 3600)
        for key in list(self.messages.keys()):
            self.messages[key] = [msg for msg in self.messages[key] if msg.get('timestamp', 0) > cutoff_time]
