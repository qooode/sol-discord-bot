"""
Manages which channels the bot is active in
"""

class ChannelManager:
    def __init__(self):
        """
        Initialize the channel manager
        """
        # Set of channel IDs where the bot is active (empty = all channels)
        self.active_channels = set()
        # Whether the bot is in channel-specific mode
        self.channel_mode = False
        # Whether the bot is globally active
        self.is_active = True
        
    def activate_channel(self, channel_id):
        """
        Activate the bot for a specific channel
        
        Args:
            channel_id: Discord channel ID
            
        Returns:
            bool: True if channel was activated, False if already active
        """
        # If first channel being added, enter channel mode
        if not self.channel_mode and len(self.active_channels) == 0:
            self.channel_mode = True
            
        # Add channel to active set
        if channel_id in self.active_channels:
            return False
        
        self.active_channels.add(channel_id)
        return True
    
    def deactivate_channel(self, channel_id):
        """
        Deactivate the bot for a specific channel
        
        Args:
            channel_id: Discord channel ID
            
        Returns:
            bool: True if channel was deactivated, False if already inactive
        """
        if channel_id not in self.active_channels:
            return False
            
        self.active_channels.remove(channel_id)
        
        # If no channels left and in channel mode, leave channel mode
        if self.channel_mode and len(self.active_channels) == 0:
            self.channel_mode = False
            
        return True
    
    def is_channel_active(self, channel_id):
        """
        Check if the bot is active in a specific channel
        
        Args:
            channel_id: Discord channel ID
            
        Returns:
            bool: True if bot is active in channel, False otherwise
        """
        # If not globally active, always return False
        if not self.is_active:
            return False
            
        # If not in channel mode, bot is active in all channels
        if not self.channel_mode:
            return True
            
        # If in channel mode, check if channel is in active set
        return channel_id in self.active_channels
        
    def set_global_active(self, active):
        """
        Set whether the bot is globally active
        
        Args:
            active: Whether to activate or deactivate the bot globally
            
        Returns:
            bool: True if state changed, False if already in that state
        """
        if self.is_active == active:
            return False
            
        self.is_active = active
        return True
    
    def get_status(self):
        """
        Get the current status of the bot
        
        Returns:
            dict: Status information
        """
        return {
            "global_active": self.is_active,
            "channel_mode": self.channel_mode,
            "active_channels": list(self.active_channels),
            "channel_count": len(self.active_channels)
        }
