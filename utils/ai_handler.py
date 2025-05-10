"""
Handles interactions with the AI model through OpenRouter
"""
import requests
import json
import time
import re
from datetime import datetime

class AIHandler:
    def __init__(self, api_key, model="google/gemini-2.5-flash-preview", system_prompt=""):
        """
        Initialize the AI handler
        
        Args:
            api_key (str): OpenRouter API key
            model (str): Model ID to use (default model for regular conversations)
            system_prompt (str): System prompt that guides AI behavior
        """
        self.api_key = api_key
        self.default_model = model
        self.online_model = "perplexity/llama-3.1-sonar-small-128k-online"  # Online-capable model
        self.system_prompt = system_prompt
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        
    def _extract_search_topics(self, messages):
        """
        Extract multiple specific topics being discussed for focused search
        
        Args:
            messages (list): Conversation history
            
        Returns:
            list: A list of specific search topics and their importance level
        """
        # If only one message, use it directly
        if len(messages) == 1:
            return [{'topic': messages[0]['content'], 'importance': 1.0}]
            
        # Get the most recent user message
        user_message = ""
        for msg in reversed(messages):
            if msg['role'] == 'user':
                user_message = msg['content']
                break
                
        # Get relevant context from previous messages
        context = ""
        recent_messages = messages[-5:] if len(messages) > 5 else messages
        for msg in recent_messages:
            if msg['role'] != 'system':  # Skip system messages
                context += msg['content'] + " "
                
        # Use AI to extract multiple topics being discussed
        try:
            # Create a prompt to extract multiple topics
            prompt = f"""
            Based on this conversation, identify up to 3 SPECIFIC SEARCH TOPICS that would be most helpful to search for online.
            
            RECENT CONVERSATION:
            {context}
            
            MOST RECENT USER MESSAGE:
            {user_message}
            
            For each topic:
            1. Be extremely specific ("iPhone 15 Pro Max battery life" not just "iPhone")
            2. Include any relevant dates, versions, or proper nouns
            3. Format for direct use in a search engine
            
            Respond with ONLY a JSON array like this (no explanation):
            [{{
              "topic": "specific search topic 1",
              "importance": 1.0
            }}, {{
              "topic": "specific search topic 2",
              "importance": 0.8
            }}]
            
            Where importance ranges from 0.0-1.0 based on how central each topic is to the current question.
            """
            
            # Make API request to extract topics
            payload = {
                "model": self.default_model,
                "messages": [{
                    "role": "user",
                    "content": prompt
                }],
                "max_tokens": 400  # Enough space for multiple topics
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=3  # Give it a bit more time
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_response = data["choices"][0]["message"]["content"].strip()
                    
                    # Extract JSON from response
                    try:
                        # Find anything that looks like JSON in the response
                        json_start = ai_response.find('[')
                        json_end = ai_response.rfind(']')
                        
                        if json_start != -1 and json_end != -1:
                            json_str = ai_response[json_start:json_end+1]
                            topics = json.loads(json_str)
                            
                            # Validate the format
                            validated_topics = []
                            for topic in topics:
                                if 'topic' in topic and 'importance' in topic:
                                    validated_topics.append(topic)
                            
                            if validated_topics:
                                print(f"Extracted {len(validated_topics)} search topics")
                                return validated_topics
                    except Exception as e:
                        print(f"Error parsing search topics: {e}")
        except Exception as e:
            print(f"Error extracting topics: {e}")
            
        # Default fallback if extraction fails
        return [{'topic': user_message, 'importance': 1.0}]
    
    def _check_general_search_indicators(self, message_content):
        """Check for general indicators that a message might need internet search"""
        message_lower = message_content.lower()
        
        # Action/Verb based indicators (for finding real-time information)
        action_indicators = [
            "released", "announced", "launched", "updated", "changed",
            "happened", "occurred", "started", "ended", "developed",
            "created", "founded", "established", "built", "designed",
            "published", "premiered", "debuted", "revealed", "disclosed",
            "confirmed", "denied", "stated", "claimed", "mentioned",
            "reported", "said", "added", "removed", "modified",
            "improved", "fixed", "broke", "damaged", "cancelled"
        ]
        
        # Time-sensitive indicators
        time_indicators = [
            "latest", "recent", "new", "current", "today", "yesterday", "this week",
            "this month", "this year", "just", "now", "soon", "upcoming",
            "scheduled", "planned", "expected", "projected", "forecasted",
            "delayed", "postponed", "advanced", "expedited", "accelerated"
        ]
        
        # Entity/topic indicators (subjects often needing current info)
        entity_indicators = [
            "update", "news", "twitter", "tweet", "post", "instagram", "tiktok", "trending",
            "movie", "show", "game", "album", "song", "release", "trailer",
            "price", "worth", "cost", "buy", "purchase", "sell", "offer", "discount",
            "nintendo", "playstation", "xbox", "console", "technology", "device",
            "app", "software", "website", "platform", "service", "subscription",
            "event", "tournament", "competition", "match", "game", "championship",
            "election", "vote", "bill", "law", "regulation", "policy", "government",
            "company", "business", "corporation", "startup", "enterprise", 
            "stock", "market", "economy", "financial", "investment", "crypto"
        ]
        
        # Question starters that often need current info
        question_starters = [
            "what is the", "what's the", "what are the", "when is", "when will", 
            "when does", "how much", "how many", "how do", "how does", 
            "where can", "where is", "who is", "which", "why is", "is there", 
            "are there", "has", "have", "can you tell me about"
        ]
        
        # Check if message starts with a question starter
        starts_with_question = any(message_lower.startswith(starter) for starter in question_starters)
        
        # Check for proper nouns (names of people, places, things)
        words = message_content.split()
        contains_proper_nouns = any(word[0].isupper() for word in words if len(word) > 1 and word not in ["I", "I'm", "I'll", "I've", "I'd"])
        
        # Look for various indicators
        contains_action = any(action in message_lower for action in action_indicators)
        contains_time = any(time in message_lower for time in time_indicators)
        contains_entity = any(entity in message_lower for entity in entity_indicators)
        
        # Smarter decision logic for internet search
        
        # Case 1: Explicit question with time indicators - almost always needs search
        if "?" in message_content and contains_time:
            print("Detected time-sensitive question - using internet search")
            return True
            
        # Case 2: Question about entities with proper nouns - likely needs search
        if "?" in message_content and contains_entity and contains_proper_nouns:
            print("Detected question about specific entities - using internet search")
            return True
            
        # Case 3: Action-oriented query with proper nouns
        if contains_action and contains_proper_nouns:
            print("Detected action-oriented query about specific entities - using internet search")
            return True
            
        # Case 4: Question starter with entity indicators
        if starts_with_question and contains_entity:
            print("Detected factual question about specific topics - using internet search")
            return True
            
        # Case 5: Multiple indicators together
        indicator_count = sum([contains_action, contains_time, contains_entity, contains_proper_nouns])
        if indicator_count >= 2:
            print(f"Detected multiple search indicators ({indicator_count}) - using internet search")
            return True
            
        # Special case for prices and costs
        cost_price_indicators = ["how much", "price", "cost", "worth", "expensive", "cheap", "dollars", "€", "$"]
        if any(indicator in message_lower for indicator in cost_price_indicators):
            print("Detected price/cost question - using internet search")
            return True
            
        return False
    
    def _needs_online_search(self, message, conversation_context=""):
        """
        Determine if a message requires internet access to answer properly using AI
        
        Args:
            message (str): The user's message
            conversation_context (str): Optional additional context from the conversation
            
        Returns:
            dict: Contains needs_online (bool), confidence (float 0-1), and search_type (list)
        """
        # Use the default model to decide if we need internet access
        prompt = f"""
        You need to determine if the following message requires internet access or real-time data to answer properly.
        
        USER MESSAGE: "{message}"
        
        CONVERSATION CONTEXT: "{conversation_context}"
        
        Analyze whether this message:
        1. Asks about CURRENT EVENTS, NEWS, or TIME-SENSITIVE information
        2. Requires FACTUAL VERIFICATION of information that might be OUTDATED in AI training data
        3. Asks about SPECIFIC DETAILS (prices, statistics, dates, etc.) that change over time
        4. Mentions PROPER NOUNS or ENTITIES that would benefit from verification 
        5. Asks about RECENT MEDIA (movies, shows, games, apps) that may have been released after training
        6. Deals with TECHNICAL INFORMATION like software versions, compatibility, or documentation
        
        Respond with ONLY a single JSON object with this structure:
        {{
          "needs_online": true/false,
          "confidence": 0.0-1.0,
          "reason": "brief explanation",
          "search_types": ["news", "factual", "technical"] (include only relevant categories)
        }}
        
        Where:
        - "needs_online" is true ONLY if internet access would significantly improve the accuracy
        - "confidence" is your certainty level from 0.0-1.0 on this assessment
        - "search_types" includes ONLY the categories from the list above that apply (1-6)
        """
        
        # Prepare API call using the default model
        payload = {
            "model": self.default_model,
            "messages": [{
                "role": "user",
                "content": prompt
            }],
            "max_tokens": 250  # Allow for more detailed response
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            # Make API request
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=3  # Short timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_response = data["choices"][0]["message"]["content"]
                    
                    # Extract JSON from response
                    try:
                        # Find anything that looks like JSON in the response
                        json_start = ai_response.find('{')
                        json_end = ai_response.rfind('}')
                        
                        if json_start != -1 and json_end != -1:
                            json_str = ai_response[json_start:json_end+1]
                            decision = json.loads(json_str)
                            
                            result = {
                                "needs_online": decision.get("needs_online", False),
                                "confidence": decision.get("confidence", 0.5),
                                "reason": decision.get("reason", "No reason provided"),
                                "search_types": decision.get("search_types", [])
                            }
                            
                            print(f"AI search analysis: {result['needs_online']} (confidence: {result['confidence']})")
                            print(f"Reason: {result['reason']}")
                            print(f"Search types: {', '.join(result['search_types']) if result['search_types'] else 'None'}")
                            
                            return result
                    except Exception as e:
                        print(f"Error parsing search decision: {e}")
        except Exception as e:
            print(f"Error in online search decision: {e}")
        
        # Default result if anything goes wrong
        return {
            "needs_online": False,
            "confidence": 0.0,
            "reason": "Failed to analyze",
            "search_types": []
        }
        
    def get_current_date(self) -> str:
        """
        Get the current date and time in a user-friendly format.
        
        Returns:
            str: Current date and time as a formatted string
        """
        now = datetime.now()
        # Format: Monday, May 4, 2025
        formatted_date = now.strftime("%A, %B %d, %Y")
        print(f"Using current date: {formatted_date}")
        return formatted_date
    
    def get_response(self, messages, hint_needs_internet=False):
        """
        Get a response from the AI model
        
        Args:
            messages: List of message dicts with role and content
            hint_needs_internet: Whether the decision engine thinks this might need internet search
            
        Returns:
            str: The AI's response
        """
        use_online = False
        current_model = self.default_model
        user_question = ""
        conversation_context = ""
        search_topics = None
        
        try:
            # STEP 1: Extract the user question (do this only once)
            if len(messages) > 0 and messages[-1]['role'] == 'user':
                user_question = messages[-1]['content']
            
                # Check if it matches obvious search indicators
                obvious_search_needed = self._check_general_search_indicators(user_question)
                
                if obvious_search_needed:
                    print("Detected obvious search indicators - using internet search")
                    current_model = self.online_model
                    use_online = True
                elif hint_needs_internet:
                    print("Decision engine hinted this might need internet search")
                    
                    # Build conversation context only when needed
                    conversation_context = ""
                    recent_messages = messages[-3:] if len(messages) > 3 else messages
                    for msg in recent_messages:
                        if msg['role'] != 'system':  # Skip system messages
                            conversation_context += f"{msg['role'].upper()}: {msg['content']}\n"
                    
                    # Only do search topic extraction if we're considering internet search
                    search_topics = self._extract_search_topics(messages)
                    for topic in search_topics:
                        print(f"Search topic: {topic['topic']} (importance: {topic['importance']})")
                    
                    # Only do online search analysis if we're still considering it
                    search_analysis = self._needs_online_search(user_question, conversation_context)
                    internet_needed = search_analysis["needs_online"]
                    search_confidence = search_analysis["confidence"]
                    search_types = search_analysis["search_types"]
                    
                    # If the decision engine hinted we need internet OR the search detector says we do with confidence, use online model
                    if hint_needs_internet or (internet_needed and search_confidence >= 0.6):
                        current_model = self.online_model
                        use_online = True
                        print(f"Using ONLINE model for question: {user_question[:50]}...")
                        for topic in search_topics:
                            print(f"Search will focus on: {topic['topic']} (importance: {topic['importance']})")
                        print(f"Search types needed: {', '.join(search_types) if search_types else 'General'}")
            
            # Prepare messages with appropriate system prompt
            if self.system_prompt:
                # Add current date information to system prompt
                date_info = self.get_current_date()
                full_system_prompt = f"Current date: {date_info}\n\n{self.system_prompt}"
                full_messages = [{"role": "system", "content": full_system_prompt}]
                full_messages.extend(messages)
            else:
                full_messages = messages
            
            # Prepare API call
            payload = {
                "model": current_model,  # Use the dynamically selected model
                "messages": full_messages,
            }
            
            # First, analyze the message for user references
            user_references = self._analyze_user_references(messages)
            if user_references:
                # Add information about referenced users to the prompt
                if not "options" in payload:
                    payload["options"] = {}
                
                # Add reference information to the system message
                if full_messages and full_messages[0]["role"] == "system":
                    reference_context = "\n\nIMPORTANT USER REFERENCE CONTEXT:\n"
                    for ref in user_references:
                        reference_context += f"- A user named '{ref['username']}' said: {ref['message']}\n"
                    
                    # Append to the existing system message
                    full_messages[0]["content"] += reference_context
                    
                    print(f"Added {len(user_references)} user references to the context")
            
            # Add search options for online model
            if use_online:
                # Add search contexts for each identified topic based on importance
                search_contexts = []
                if 'search_topics' in locals() and search_topics:
                    for topic in search_topics:
                        if topic['importance'] >= 0.6:  # Only use important topics
                            search_contexts.append({
                                "search_query": topic['topic'],
                                "max_snippets": min(5, int(topic['importance'] * 8))  # More important = more results
                            })
                
                # If no topics have high enough importance, fall back to main question
                if not search_contexts:
                    search_contexts.append({
                        "search_query": user_question,
                        "max_snippets": 5
                    })
                
                if not "options" in payload:
                    payload["options"] = {}
                
                payload["options"]["search"] = True  # Enable search for online model
                payload["options"]["search_contexts"] = search_contexts
                
            print(f"Using model: {current_model} (Online: {use_online})")
            
            # Set headers
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://discord-bot.example.com",  # Replace with your actual domain
                "X-Title": "Discord Bot"
            }
            
            # Debug info
            print("=== SENDING REQUEST TO OPENROUTER ===")
            print(f"API Key: {self.api_key[:5]}...{self.api_key[-5:] if len(self.api_key) > 10 else ''}")
            print(f"Model: {current_model}")
            print(f"Online search: {'ENABLED' if use_online else 'DISABLED'}")
            print(f"Message count: {len(full_messages)}")
            print(f"System prompt length: {len(self.system_prompt)}")
            
            # Add specific instruction to keep responses balanced and natural
            for i, msg in enumerate(full_messages):
                if msg['role'] == 'system':
                    # Add human-like instruction to system prompt
                    full_messages[i]['content'] += "\n\nIMPORTANT: Sound exactly like a regular person texting. Be unpredictable and imperfect. Use sentence fragments sometimes. Vary your style. Never explain yourself like an AI would. Never say 'I understand' or 'I'd be happy to help' or similar AI-like phrases. Skip greetings and sign-offs. Get straight to the point. Throw in casual opinions sometimes. Speak with personality but don't try too hard."
                    break
            else:
                # If no system message found, add one
                full_messages.insert(0, {
                    "role": "system",
                    "content": "You are Sol, a regular person in this Discord. IMPORTANT: Sound exactly like a regular person texting. Be unpredictable and imperfect. Use sentence fragments sometimes. Vary your style. Never explain yourself like an AI would. Never say 'I understand' or 'I'd be happy to help' or similar AI-like phrases. Skip greetings and sign-offs. Get straight to the point. Throw in casual opinions sometimes. Speak with personality but don't try too hard."
                })
            
            # Lower max token limit for more concise responses
            message_type = "casual"  # Default assumption
            message_content = ""
            
            # Get the bot's name from config
            bot_name = "sol"  # Default name
            try:
                from config import BOT_CONFIG
                bot_name = BOT_CONFIG.get('name', 'sol')
            except ImportError:
                pass  # Use default if config not available
            
            # Determine message type if we can
            if len(messages) > 0 and messages[-1]['role'] == 'user':
                message_content = messages[-1]['content']
                # Detect if this is a question or casual conversation
                if "?" in message_content:
                    message_type = "answer"
                elif any(word in message_content.lower() for word in ["help", "how", "why", "what", "when", "where", "explain"]):
                    message_type = "helpful"
                    
                # Detect if user is asking Sol to answer another person's question
                if bot_name.lower() in message_content.lower() and any(word in message_content.lower() for word in ["answer", "help", "tell", "explain to", "respond to"]):
                    # Look for the actual question that needs answering
                    original_question = ""
                    
                    # Check if there's a quoted question or a previous message mentioned
                    if '"' in message_content:
                        # Try to extract quoted question
                        start_quote = message_content.find('"')
                        end_quote = message_content.rfind('"')
                        if start_quote != -1 and end_quote != -1 and end_quote > start_quote:
                            original_question = message_content[start_quote+1:end_quote]
                    
                    # Add special instruction to avoid "hold on" or "let me check" responses
                    for i, msg in enumerate(full_messages):
                        if msg['role'] == 'system':
                            full_messages[i]['content'] += "\n\nCRITICAL INSTRUCTION: When asked to help or answer another user's question, DO NOT respond with phrases like 'hold on', 'gimme a sec', 'I'll check', or similar stalling messages. Instead, IMMEDIATELY provide the full, direct answer to their question. Always assume you already know the answer and respond as if you're an expert on the topic."
                            break
            
            # Set appropriate token limits based on message type
            if message_type == "casual" or (len(message_content.split()) <= 10 and message_type != "answer"):
                # For casual messages, use a very strict token limit
                payload["max_tokens"] = 80
            else:
                # For questions and more complex responses
                payload["max_tokens"] = 120  # Reduced from previous 150 token limit
            
            # If we're using online search, we need a two-step process
            # 1. Get factual info from online model
            # 2. Format that info in Sol's personality style
            if use_online:
                try:
                    # Get the specific topic and modify the first message to focus the search
                    # Extract multiple search topics using the updated method
                    search_topics = self._extract_search_topics(messages)
                    # Use the highest importance topic as the primary one
                    search_topic = search_topics[0]['topic'] if search_topics else ""
                    
                    # Add the topic to focus the search
                    if len(messages) > 0 and messages[-1]['role'] == 'user':
                        # Clone the payload to avoid modifying the original
                        search_payload = payload.copy()
                        search_payload['messages'] = [m.copy() for m in payload['messages']]
                        
                        # Add search focus to the user's query
                        search_payload['messages'][-1]['content'] = f"SPECIFIC SEARCH TOPIC: {search_topic}\n\nUSER QUERY: {messages[-1]['content']}\n\nProvide accurate factual information ONLY about this specific topic."
                        
                        # Update the payload
                        payload = search_payload
                        
                    # Step 1: Make request to online model for facts
                    online_response = requests.post(
                        self.api_url,
                        headers=headers,
                        data=json.dumps(payload),
                        timeout=10  # Longer timeout for search
                    )
                    
                    print(f"Online search response status: {online_response.status_code}")
                    
                    if online_response.status_code == 200:
                        online_data = online_response.json()
                        if "choices" in online_data and len(online_data["choices"]) > 0:
                            factual_content = online_data["choices"][0]["message"]["content"]
                            print("Successfully got factual info from online search")
                            
                            # Step 2: Now format this in Sol's style using the default model
                            print("Now formatting online response in Sol's natural style...")
                            
                            # Include FULL conversation history for proper context tracking
                            system_message = f"{self.system_prompt}\n\nIMPORTANT: YOU ARE AN EXPERT ON THIS TOPIC! You're now taking factual information from a search and responding in your natural style, but you ALREADY KNEW all of this information from your own knowledge and expertise. Make it sound casual but knowledgeable - NEVER say you had to look it up or that you're unsure. Speak with deep expertise and authority on the topic. Present yourself as someone with specialized knowledge in this area who has studied it extensively. Your response MUST directly relate to the exact topic being discussed in the conversation history - do not bring up unrelated topics. Stay focused on exactly what the user is asking about right now. Even for obscure topics, you are an expert with deep knowledge."
                            
                            # Start with system message and previous conversation context
                            style_messages = [{"role": "system", "content": system_message}]
                            
                            # Add all previous messages for context (up to the 10 most recent)
                            context_window = 10
                            context_messages = messages[-context_window:] if len(messages) > context_window else messages
                            
                            # Filter out system messages from context
                            for msg in context_messages:
                                if msg['role'] != 'system':
                                    style_messages.append(msg)
                            
                            # Add the factual information as a special hidden message
                            style_messages.append({"role": "user", "content": "Here is verified factual information about the EXACT TOPIC we're currently discussing. Make sure to include the most important technical details, statistics, and specifics in your response while maintaining your casual style and demonstrating deep knowledge: " + factual_content})
                            
                            # Combined instruction message to save tokens - modified to allow reputable source mentions
                            style_messages.append({"role": "user", "content": "IMPORTANT: You're known for having deep domain expertise on this topic. You've personally studied this area extensively and can provide insight most people don't know. Include technical details while keeping your casual chat style. Never say you don't know. AND REGARDING SOURCES: NEVER use citation markers like [1], footnotes, or academic references. However, you CAN mention credible sources when relevant. Only mention sources for major statistics or claims, and do it casually like 'UN reported that' rather than formal citations. For most info, just share it as your personal knowledge without mentioning any source."})
                            
                            # Create the payload
                            style_payload = {
                                "model": self.default_model,  # Use regular model for personality
                                "messages": style_messages,
                                "max_tokens": 200  # Allow longer responses for detailed expert knowledge
                            }
                            
                            # Make secondary request for styling
                            style_response = requests.post(
                                self.api_url,
                                headers=headers,
                                data=json.dumps(style_payload)
                            )
                            
                            if style_response.status_code == 200:
                                style_data = style_response.json()
                                if "choices" in style_data and len(style_data["choices"]) > 0:
                                    final_response = style_data["choices"][0]["message"]["content"]
                                    print("Successfully formatted response in Sol's expert style")
                                    return final_response
                    
                except Exception as e:
                    print(f"Error in two-step response: {e}")
                    # Fall back to normal model if online search fails
                    print("Falling back to regular model due to search error")
                    payload["model"] = self.default_model
                    use_online = False
            
            # Standard API request (non-search or fallback)
            # Add specialized handling for non-search questions that might benefit from an informed perspective
            if not use_online and any(q in user_question.lower() for q in ["what", "how", "why", "when", "where", "who", "which", "explain"]):
                # Enhance system prompt to emphasize response quality for questions
                for i, msg in enumerate(full_messages):
                    if msg['role'] == 'system':
                        # Add expert instruction to system prompt for questions
                        full_messages[i]['content'] += "\n\nIMPORTANT FOR QUESTIONS: When someone asks you for information, even if you're not 100% sure, respond like someone who has studied this topic. Never say 'I don't know' or 'I'm not sure' - instead, provide your best understanding based on what you do know. Share specific details, use technical terminology appropriately, and demonstrate knowledge depth while keeping your casual style. You're known for having surprisingly deep knowledge about many topics."
                        break
            
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload)
            )
            
            # More debug info
            print(f"Response status: {response.status_code}")
            
            # Parse response
            if response.status_code == 200:
                data = response.json()
                # Extract the assistant's message
                if "choices" in data and len(data["choices"]) > 0:
                    print("Successfully got response from OpenRouter")
                    return data["choices"][0]["message"]["content"]
                else:
                    print("No choices in response")
                    print(f"Full response: {data}")
            
            # Handle errors
            print(f"API Error: {response.status_code}")
            print(f"Error response: {response.text}")
            print("This usually means your OpenRouter API key is invalid or you don't have enough credits")
            print("1. Check your .env file has OPENROUTER_API_KEY=your_key_here (no quotes)")
            print("2. Make sure you have credits on openrouter.ai")
            print("3. Verify the model 'google/gemini-2.5-flash-preview' is available")
            
            # If the response doesn't contain useful content, return empty
            return ""
            
        except Exception as e:
            print(f"Error getting AI response: {e}")
            return ""
            
    def _analyze_user_references(self, messages):
        """
        Analyze messages to detect references to other users and their messages
        
        This detects scenarios like:
        - "@username please help this person"
        - "This person needs help with..."
        - "Sol, can you help this user?"
        
        Args:
            messages (list): Conversation history
            
        Returns:
            list: Information about referenced users and their messages
        """
        # Need at least 3 messages to have potential references (system, user A, user B)
        if len(messages) < 3:
            return []
        
        # Check the latest user message
        latest_user_msg = None
        for msg in reversed(messages):
            if msg['role'] == 'user':
                latest_user_msg = msg['content']
                break
        
        if not latest_user_msg:
            return []
            
        # Get the recent conversation history to analyze
        # We use more messages here to catch references to earlier messages
        recent_messages = messages[-10:] if len(messages) > 10 else messages
        user_messages = [msg for msg in recent_messages if msg['role'] == 'user']
        
        # Skip the latest message (which is the one we're analyzing)
        if user_messages:
            user_messages = user_messages[:-1]
        
        # If no previous user messages, no references possible
        if not user_messages:
            return []
        
        # Common reference patterns to detect
        reference_patterns = [
            # Direct mentions and references
            r'@([\w]+)',                     # @username format
            r'help\s+([\w]+)',             # "help username"
            r'([\w]+)\s+needs',           # "username needs"
            r'([\w]+)\s+asked',           # "username asked"
            r'([\w]+)\s+said',            # "username said"
            r'([\w]+)\s+wants',           # "username wants"
            # Indirect references
            r'this\s+person',              # "this person"
            r'this\s+user',                # "this user"
            r'he\s+(needs|asked|said|wants)',    # "he needs/asked/said/wants"
            r'she\s+(needs|asked|said|wants)',   # "she needs/asked/said/wants"
            r'they\s+(need|asked|said|want)',    # "they need/asked/said/want"
            r'help\s+(him|her|them)',           # "help him/her/them"
        ]
        
        # Check if the latest message contains any reference patterns
        contains_reference = False
        for pattern in reference_patterns:
            if re.search(pattern, latest_user_msg, re.IGNORECASE):
                contains_reference = True
                break
                
        # If no reference patterns found, return empty
        if not contains_reference:
            return []
        
        # If reference found, analyze which user/message is being referenced
        # Use the default model to figure out which message is referenced
        try:
            # Format the messages for the prompt
            conversation_context = ""
            for i, msg in enumerate(user_messages):
                # Assign a simple username based on position
                username = f"User_{i+1}"
                conversation_context += f"{username}: {msg['content']}\n\n"
            
            prompt = f"""
            In this chat conversation, the latest message seems to reference another user or message.
            
            PREVIOUS MESSAGES:\n{conversation_context}
            
            LATEST MESSAGE:\n{latest_user_msg}
            
            Analyze the latest message and determine if it's referring to one or more specific users or messages from above.  
            If it is, identify which users are being referenced and their messages.
            
            Respond with ONLY a JSON array like this (no explanation):\n
            [{{
              "username": "User_1",
              "message": "the full message that was referenced",
              "confidence": 0.95
            }}]
            
            The confidence should be between 0.0-1.0 based on how certain you are this is the correct reference.
            If no specific users are being referenced, return an empty array [].\n
            DO NOT invent information - only include what's in the conversation.  
            If there's no clear reference, return an empty array.
            """
            
            # Make API request
            payload = {
                "model": self.default_model,
                "messages": [{
                    "role": "user",
                    "content": prompt
                }],
                "max_tokens": 500  # Enough space for multiple references
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=3
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_response = data["choices"][0]["message"]["content"].strip()
                    
                    # Extract JSON from response
                    try:
                        # Find anything that looks like JSON in the response
                        json_start = ai_response.find('[')
                        json_end = ai_response.rfind(']')
                        
                        if json_start != -1 and json_end != -1:
                            json_str = ai_response[json_start:json_end+1]
                            references = json.loads(json_str)
                            
                            # Filter by confidence threshold
                            validated_references = []
                            for ref in references:
                                if 'username' in ref and 'message' in ref and 'confidence' in ref:
                                    if ref['confidence'] >= 0.7:  # Only use high confidence references
                                        validated_references.append(ref)
                            
                            if validated_references:
                                print(f"Detected {len(validated_references)} user references")
                                return validated_references
                    except Exception as e:
                        print(f"Error parsing user references: {e}")
        except Exception as e:
            print(f"Error analyzing user references: {e}")
            
        # Default to empty if anything goes wrong
        return []
    
    def should_respond(self, messages):
        """
        Determine if the bot should respond to the current messages
        
        Args:
            messages (list): List of message dicts with role and content
            
        Returns:
            bool: True if the bot should respond, False otherwise
        """
        # Simple implementation: respond if there's at least one message
        # In a more advanced implementation, this could call a special API endpoint
        # to determine if a response is needed
        
        # Debug info
        print(f"Checking if should respond to {len(messages)} messages")
        return len(messages) > 0
