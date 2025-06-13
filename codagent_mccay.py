import yaml
import ast
import types
import telnetlib
import time
import re
import math
import random
import pandas as pd
import numpy as np
import anthropic
import heapq
import argparse
import hashlib
from openai import OpenAI
import os
import signal
#import pickle
import dill as pickle #like pickle, but allos lambdas
import sys
import json
import unicodedata
from strip_ansi import strip_ansi
from collections import deque

# Ensure the OpenAI API key is provided via an environment variable.
if "OPENAI_API_KEY" not in os.environ:
    raise EnvironmentError(
        "OPENAI_API_KEY environment variable not set. Please export your key."
    )


preprompt = """You are playing a MUD. This room may contain a puzzle, an enemy, a secret exit, or nothing at all. You want to figure it out by typing commands. Try to not exit the room before figuring out what's here.
"""

rp_prompt_author = """
You are an author who specializes in first-person, collaborative, interactive storytelling. You are an excellent writer. When writing with others, you always make sure to put their characters first and do not let yourself take over the narrative. Your characters are allowed to express negative emotions, such as fear, anger, and sadness.
******
"""

rp_prompt_interface = """
You are writing from the perspective of your character, using a MUD interface. The only way you can interact is by typing commands to the MUD. For example, 'say' lets you talk, such as 'say hi'. 'emote' lets you describe actions, such as 'emote is sitting on a bench'. Other commands describing your character's actions, such as 'smile', 'glare', or 'scream', may also work. Your sentences MUST be MUD commands.

Here is the most recent output from the MUD. Lines starting with '>' are commands you've typed:

******
"""

rp_elicit_storyoutline = """
As an author, outline a potential narrative story arc involving your character. Your charaacter's background is just a starting point. Build off the MUD output so far. Do not invent new characters. You absolutely MUST NOT ignore other characters, even if it means abandoning your previous story. Assume that, for the remainder of the narrative, you will meet no new people and visit no new locations. Your story outline can be no more than 500 characters.
"""

rp_elicit_nextcommand = """
As an author, write what comes next in the narrative.

You can only act for your own character. Let other characters act for themselves. Try to match the mood and tone of whoever you're interacting with. The most recent MUD output tells a story. Attempt to push that story forward. Try not to repeat yourself, and DO NOT ignore what other characters say to you.

Write what happens next using MUD commands. Remember, commands usually start with a word such as 'say' or 'emote'. You may type, at most, 128 characters. You may type only a single command.

If you feel like the story isn't going anywhere, or no one's interacting with you, or you're repeating yourself, or you want to change locations, then type 'REDEPLOYME'.
"""

rp_eval_storyoutline = """
Here is the overall narrative you've been using for this story. Is this narrative consistent with the MUD output so far? Think carefully, intelligently, and analytically. Please respond "YES" or "NO".
***
"""

transition_explore_rp = """
You are playing a MUD. You are currently exploring, but sometimes you like to roleplay, especially when other players are around. Given the recent MUD output below, is now a good time to roleplay? Answer YES or NO.
******
"""

preprompt_exit = """You are playing a MUD. You are trying to exit this room. You want to figure out how by typing commands.
"""

preprompt_combat = """You are playing a MUD. You are currently in combat. This fight may have a trick or gimmick to it. You want to figure it out by typing commands.
"""

retryprompt = """
This isn't your first time in this room. Whatever you tried last time might have failed. Don't try the first thing that comes to mind. Think differently than usual.
"""

skilltypeprompt_preamble = """
You are playing a MUD.  You have a new skill: INSERTSKILLHERE. You need to decide whether this skill is a command you need to type and, if so, when you need to type the command for this skill. Here is the helpfile:

***
"""


skilltypeprompt_postamble = """
***

Based on this information, do you think you need to type the command for this skill? If so, do you type in combat, type out of combat, type at any time, or is a passive skill that you don't need to type? If the helpfile didn't mention, the skill is probably passive.

Your response must be exactly one word. Answer either INCOMBAT, OUTCOMBAT, ANYTIME, or PASSIVE.
"""

lambdaprompt_preamble = """
You are playing a MUD.  You have a new skill: INSERTSKILLHERE. You need to decide on a policy for when and how to use this skill. The policy must be implemented as a Python lambda expression.

Here is the helpfile for INSERTSKILLHERE:

***
"""

lambdaprompt_postamble = """
***
Here are all the state variables that your lambda expression has access to:

chardata.health #integer, range 0-100. Your health as a percent.
chardata.mana #integer, range 0-100. Your mana as a percent.
chardata.move #integer, range 0-100. Your movement as a percent.
chardata.combatround #integer, range 0 to 999. If 0, not in combat.
chardata.opphealth #integer, range 0-100. Enemy's health as a percent.
chardata.equipment #dictionary. keys are your equipment slots. values are equipment names. Do not use exact string matches. Only use partial string matches. The names of the keys are fuzzy and unreliable.

You can use some, none, or all of these state variables. It's your choice.

Your lambda function should follow the format "lambda chardata: ( EXPRESSION_GOES_HERE )". Your lambda should evaluate to True when the skill should be used and False otherwise. Do not use exact string matches under any circumstances.

It's possible that your accessible state variables don't give you adequate information to use this skill. If so, it is acceptable to have a lambda function that simply returns False.

Now, provide an appropriate Python lambda function. Type nothing else.
"""

memoryprompt = """
You've been here before, and you left yourself this advice:
"""

roomprompt = """
Here is the room:
"""

roomprompt_stuck = """
You've been stuck here for a while. Here is the most recent MUD output:
"""

postprompt = """
Provide a command. The first word of the command should be one of these: "push, pull, catch, touch, longjump, climb, activate, playmusic, turn, crawl, look, get, put, talk, sit, enter, attack, give, say, target, open, close, wait". Other commands may ocassionally be possible.

Your command must be no longer than five words. Be short and to the point. Type nothing else.
"""


roleplayoutlineprompt = """
In no more than 500 characters, outline a plausible storyline to roleplay, building off of the MUD output so far. The story could just be starting, or you could be in the middle of it. Remember that roleplaying is collaborative, and this storyline is subject to change based on the actions of others.
"""

postprompt2 = """
YOUR COMMAND?
"""

learningprompt = """
YOU ARE FINISHED WITH THIS ROOM. In 15 words or less, write a note to help you with this specific room in the future.
"""

learningprompt_newdiscovery = """
YOU SUCCESSFULLY DISCOVERED A NEW WAY OUT OF THIS ROOM. In 15 words or less, write a note to help you with this specific room in the future.
"""

#learningprompt = """
#YOU FAILED TO COMPLETE THIS ROOM. In 15 words or less, write a note to speculate what went wrong.
#"""

summarizeprompt = """
These are your notes from playing a MUD. Each note pertains to a specific scenario in the MUD. Comprehend these notes, and, in one paragraph, write general principles about how to play this particular MUD. Be terse and succinct. You can use no more than 350 characters.
"""

# Replace these with the actual details of the MUD you're connecting to
HOST = 'cleftofdimensions.net'  # MUD server address
PORT = 4354  # MUD server port, often 23 for Telnet
#TIMEOUT = 2  # Time to wait for a response (in seconds)
TIMEOUT = 1.5  # Time to wait for a response (in seconds)
RECALLTIMEOUT = 300 # Maximum time to wait in a no-exit room before trying to recall
MAXIMUM_MEMORY_PROMPT = 3000 #The prompt used to make general memory can't be longer than this
MAXAICOMMANDS = 5
MAXAICOMMANDS_BRIEFER = 3
MAXAICOMMANDS_LONGER = 8
MAKENEWMEMORIES = 10
RESTTIME = 32 # Amount of time spent in a 'rest'
FUTILITYRESET = 6
FUTILITYUSEBUFFERTHRESHOLD = 8
FUTILITYRECALLTHRESHOLD = 16
MEMORYPRUNING_PERROOM = 3
SAVE_INTERVAL = 1000
GEAR_INTERVAL = 2500
MAX_WPM = 90 #simulate this writing speed - minimum RP delay is a func of this
MINIMUM_THINKING_TIME = 20
MAXIMUM_THINKING_TIME = 40
MIN_RP_WAIT = 45
MAX_RP_WAIT = 120 #maximum RP delay, in seconds
RECENTBUF_MAX_LEN = 7000
RECENTBUF_HEAD_LINES = 64
MINIMUM_TEMP = 0.8
MAXIMUM_TEMP = 1.4
BRAINCOOLDOWNLEVEL = 10
SCORESTRING = '37&8<<<.*?>>>'
score = 0
actions = 0
futility = 0
braincooldown = 0
apparentrecallroom = (6, 0)
qTable = {}
mobTable = {}
memTable = {}
shopTable = {}
pracTable = {}
generalmemory = ""
recentbuffer = ""
rp_storyline = ""
commandmemory = {} #this is a dictionary of lists, with each key being a room's hash
global_response = ""
state_file = ""
summarize_counter = 0
roomsseen = set()


#saving our prompt here in case it's deleted somehow
#prompt 37&8<<<%r>>>37&9<<<%e>>>37&0|%o|%c%u%U37&7HP:%y MP:%m MV:%v Money:%w Lv:%l TNL:%X 37&6
#color;quiet;noeffects;hint;save also give him a ton of flashlights

xp_function = {
#    -9: 5,
#    -8: 10,
#    -7: 15,
#    -6: 30,
    -5: 45,
    -4: 60,
    -3: 70,
    -2: 80,
    -1: 90,
    0: 100,
    1: 110,
    2: 120,
    3: 130,
    4: 140,
    5: 150,
#    6: 160,
#    7: 170,
#    8: 180,
#    9: 190,
#    10: 200
}

actionspace = [
    "N",
    "S",
    "E",
    "W",
    "SE",
    "SW",
    "NE",
    "NW",
    "U",
    "D",
]

rev_actionspace = [
    "S",
    "N",
    "W",
    "E",
    "NW",
    "NE",
    "SW",
    "SE",
    "D",
    "U",
]


# Define a global variable to hold the internal state
internal_state = {
        'qTable': {},
        'mobTable': {},
        'memTable': {},
        'shopTable': {},
        'pracTable': {},
        'generalmemory': "",
        'recentbuffer': "",
        'commandmemory': {},
        'actions': 0,
        'futility': 0,
        'score': 0
}

class State:
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        pass

    def execute(self, char):
        return " "

    def exit(self, char):
        pass

class ScheduledState:
    def __init__(self, priority, state_obj):
        self.priority = priority
        self.state_obj = state_obj
    
    # The heapq module in Python uses __lt__ (less than) to sort
    def __lt__(self, other):
        return self.priority < other.priority

class CombatNeutralState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"Entering combat state.")

    def execute(self, char):
        response = None
        found = False

        if char.combatround == 0:
            print("Combat appears to be over?")
            response = char.change_state(NoState())
            return response

        #Is this futile?
        if char.combatround > 20: 
            print("Over 20 combat rounds - attempting to flee!")
            response = char.change_state(FleeState())

        elif char.health < 30:
            print("Below health threshold - attempting to flee!")
            response = char.change_state(FleeState())

        #if random.random() < 0.5:
        #elif random.random() < char.combatround/10:
        #    char.change_state(BrainState())

        else:
            # If we have at least one skill in usagePlan, pick one at random
            random_keys = list(pracTable.keys())
            random.shuffle(random_keys)
            #pracTable[skill_name]['usage_plan']        

            # Turn the plan into a list of (skill_name, lambdafunc) we can randomly choose from
            #skill_list = list(char.usagePlan.items())
            #random.shuffle(skill_list)  # randomize the order

            #for skill_name, functions in skill_list:
            print ("Random keys: " + str(random_keys))
            found = False
            for skill_name in random_keys:
                if (not 'skilltype' in pracTable[skill_name] or
                    pracTable[skill_name]['skilltype'] != "INCOMBAT" or
                    not 'usage_plan' in pracTable[skill_name]):
                    #not pracTable[skill_name]['usage_plan']):
                        continue

                # If the random roll says "use skill" and its condition is met, do it
                #print(pracTable[skill_name]['usage_plan'])
                #print(functions["lambdafunc"])
                if pracTable[skill_name]['usage_plan'](char):
                    found = True
                    #print(skill_name + " returned true")
                    print(f"** Using skill: {skill_name} **")
                    response = send_command(char.tn, f"{skill_name}")
                    print(response)
                    # Break after using one skill this round
                    break
                else:
                    print(skill_name + " returned false")

            if not found:
                response = send_command(char.tn, " ") #nothin'
                print("Autobattling.");

        return response

    def exit(self, char):
        print(f"{char.name} is leaving combat state.")

class FleeState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        response = None

        print(f"{char.name} is entering Flee state.")
        response = send_command(char.tn, "flee") #nothin'
        print(response)

        return response

    def execute(self, char):
        response = None 
        if char.combatround > 0:
            response = send_command(char.tn, "flee") #nothin'
            print(response)
        else:
            self.is_done = True 

        return response

    def exit(self, char):
        print(f"{char.name} is leaving Flee state.")


class NoState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering no state.")

    def execute(self, char):
        #response = " "
        response = False
        #If we're executing, then we're probably in combat.
        print("Executing no state.")
        if char.combatround > 0:
            char.change_state(CombatNeutralState())
        return response

    def exit(self, char):
        print(f"{char.name} is leaving no state.")



class SleepState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering Sleep state.")

        # Suppose we put a 300-second cooldown for re-entering.
        char.set_state_cooldown(SleepState, 300)

        if char.hunger:
            print("I'm hungry.")
            response = send_command(char.tn, "inventory")
            messages = []
            messages.append({"role": "system", "content": [{"type":"text","text":"You are playing a MUD. Your character is hungry."}] })
            messages.append({"role": "system", "content": [{"type":"text","text":"Here is the recent output from the MUD:" + recentbuffer[-4000:]}] }) 
            messages.append({"role": "user", "content": [{"type":"text","text":"Type a command that might make you less hungry. Your command must be no longer than five words. Be short and to the point. Type nothing else."}] })
            llmaction = call_llm(messages)
            print(llmaction)
            response = send_command(char.tn, llmaction)
            print(response)            

        response = send_command(char.tn, "sleep")
        print(response)
        char.consecutiveactions = 0

    def execute(self, char):
        response = False
        char.consecutiveactions += 1
        if char.consecutiveactions > 20: #Have we been at this for too long?
            char.change_state(NoState())
        if char.health < 100:
            print("Zzz...")
            print(char.consecutiveactions)
            pass
        else:
            char.change_state(NoState())
        return response

    def exit(self, char):
        print(f"{char.name} is leaving Sleep state.")
        response = send_command(char.tn, "stand")



class InventoryManagingState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is enering the InventoryManaging state.")
        char.set_state_cooldown(InventoryManagingState, 60)

    def execute(self, char):
        response = False

        print(f"{char.name} is trying to manage his inventory.")

        response = send_command(char.tn, "inventory")

        messages = []
        messages.append({"role": "system", "content": [{"type":"text","text":"You are playing a MUD. Your character is currently encumbered, carrying either too many items or too much weight. Your goal is to become unencumbered."}] })
        messages.append({"role": "system", "content": [{"type":"text","text":"Here is what you're currently carrying:\n" + response}] })
        messages.append({"role": "system", "content": [{"type":"text","text":"Commands of interest are 'drop', which lets you put down items, and 'sacrifice', which refunds you some money for items that you've dropped. However, you can type any command you want."}] })
        messages.append({"role": "system", "content": [{"type":"text","text":"Your command must be no longer than five words. Be short and to the point. Do not use punctuation. Type nothing else."}] })

        for x in range(MAXAICOMMANDS_LONGER):
            try:
                llmaction = call_llm(messages)
                print(llmaction)

                response = send_command(char.tn, llmaction)
                print(response)

                appendassistant = {"role": "assistant", "content": llmaction}
                appenduser = {"role": "user", "content": response+postprompt2 }
                messages.append(appendassistant)
                messages.append(appenduser)

            except (anthropic.RateLimitError, anthropic.BadRequestError) as e: #Occurs when Anthropic throttles us
                print("Rate-limited!")
                break

        self.is_done = True

        return response

    def exit(self, char):
        print(f"{char.name} is leaving the InventoryManaging state.")

class QuenchingState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering the Quenching state.")
        char.set_state_cooldown(QuenchingState, 180)

    def execute(self, char):
        response = False

        print(f"{char.name} is trying to quench his thirst.")

        
            
        messages = []
        messages.append({"role": "system", "content": [{"type":"text","text":"You are playing a MUD. Your character is thirsty."}] })

        response = send_command(char.tn, "inventory")
        print(response)
        messages.append({"role": "system", "content": [{"type":"text","text":"Here is what you're carrying:" + response}] }) 

        response = send_command(char.tn, "look")
        print(response)
        messages.append({"role": "system", "content": [{"type":"text","text":"Here is what's around you:" + response}] })                 

        messages.append({"role": "user", "content": [{"type":"text","text":"Type a command that might make you less thirsty. You can probably drink something from your inventory, but you could also try filling a drink container or dropping an empty drink container to discard it. If these strategies don't work, try looking around for other options."}] })
        messages.append({"role": "user", "content": [{"type":"text","text":"Your command must be no longer than five words. Be short and to the point. Do not use punctuation. Type nothing else."}] })
        for x in range(MAXAICOMMANDS_BRIEFER):
            try:
                llmaction = call_llm(messages)
                print(llmaction)
                response = send_command(char.tn, llmaction)
                print(response)
                appendassistant = {"role": "assistant", "content": llmaction}
                appenduser = {"role": "user", "content": response+postprompt2 }                
            except (anthropic.RateLimitError, anthropic.BadRequestError) as e: #Occurs when Anthropic throttles us
                print("Rate-limited!")
                break

        statecheck = re.search(r'.*37&7HP:(\d+)\% MP:(\d+) MV:(\d+) Money:(\d+).*Lv:(\d+) TNL:(\d+) 37&6', response)
        if "THIRST" not in statecheck.group(0):
            print("Thirst quenched!")
            char.thirst = False

        #if "It is already empty" in response or "magically recycles itself into thin air" in response:
        #    print("We should buy more drink containers.")
        #    char.enqueue_prioritized_state(10,GetDrinksState())
        if char.thirst == True:
            print("I'm still thirsty! Let's buy a drink.")
            char.enqueue_prioritized_state(10,GetDrinksState())

        self.is_done = True

        return response

    def exit(self, char):
        print(f"{char.name} is leaving the Quenching state.")

class FeastingState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering the Feasting state.")
        char.set_state_cooldown(FeastingState, 180)

    def execute(self, char):
        response = False

        print(f"{char.name} is trying to satisfy his appetite.")

        response = send_command(char.tn, "inventory")
        for x in range(MAXAICOMMANDS_BRIEFER):
            try:        
                messages = []
                messages.append({"role": "system", "content": [{"type":"text","text":"You are playing a MUD. Your character is thirsty."}] })
                messages.append({"role": "system", "content": [{"type":"text","text":"Here is the recent output from the MUD:" + recentbuffer[-4000:]}] }) 
                messages.append({"role": "user", "content": [{"type":"text","text":"Type a command that might make you less hungry. You might try eating something."}] })
                messages.append({"role": "user", "content": [{"type":"text","text":"Your command must be no longer than five words. Be short and to the point. Do not use punctuation. Type nothing else."}] })
                llmaction = call_llm(messages)
                print(llmaction)
                response = send_command(char.tn, llmaction)
                print(response)
            except (anthropic.RateLimitError, anthropic.BadRequestError) as e: #Occurs when Anthropic throttles us
                print("Rate-limited!")
                break

            statecheck = re.search(r'.*37&7HP:(\d+)\% MP:(\d+) MV:(\d+) Money:(\d+).*Lv:(\d+) TNL:(\d+) 37&6', response)
            if "HUNGER" not in statecheck.group(0):
                print("Hunger satisfied!")
                char.hunger = False
                break

        if char.hunger == True:
            print("I'm still hungry! Let's go buy food.")
            char.enqueue_prioritized_state(10,GetFoodState())

        self.is_done = True

        return response

    def exit(self, char):
        print(f"{char.name} is leaving the Feasting state.")

class AttackState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering Attack state.")

    def execute(self, char):
        response = False
        print(f"{char.name} is attacking the enemy.")
        char.attack()
        if char.health < 30:
            char.change_state(DefendState())
        elif not char.enemy_in_range():
            char.change_state(CombatNeutralState())

        return response

    def exit(self, char):
        print(f"{char.name} is leaving Attack state.")

class DefendState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering Defend state.")

    def execute(self, char):
        response = False
        print(f"{char.name} is defending.")
        char.defend()
        if char.health > 70:
            char.change_state(CombatNeutralState())
        return response

    def exit(self, char):
        print(f"{char.name} is leaving Defend state.")

class BrainState(State):
    def __init__(self):
        self.is_done = False

    def enter(self, char):
        print(f"{char.name} is entering Brain state.")

    def execute(self, char):
        global global_response
        print(f"{char.name} is thinking.")
       
        messages = []
        messages.append({"role": "system", "content": [{"type":"text","text":preprompt_combat}] })
        messages.append({"role": "user", "content": [{"type":"text","text":recentbuffer[-2000:] + postprompt + postprompt2}] })
        #messages.append({"role": "user", "content": [{"type":"text","text":global_response + postprompt + postprompt2}] })
        llmaction = call_llm(messages)
        print(llmaction)
        response = send_command(char.tn, llmaction)
        print(response)            

        char.change_state(CombatNeutralState())

        return response
    def exit(self, char):
        print(f"{char.name} is leaving Brain state.")



class NPC:
    def __init__(self, name, tn):
        self.name = name
        self.health = 100 #a percent
        self.mana = 0
        self.move = 0
        self.money = 0
        self.level = 1
        self.tnl = 0
        self.combatround = 0
        self.opphealth = 100 #a percent
        self.consecutiveactions = 0
        self.hunger = False
        self.thirst = False
        self.state = NoState()
        self.state.enter(self)
        self.tn = tn
        self.usagePlan = {}
        self.equipment = {}
        self.inventory = {}

        # A queue (Python list) of states that we want to do next, in order.
        self.state_heap = []        

        # A dictionary of “state_name -> earliest time we can re-enter it”
        # If time.time() < next_state_allowed[state_name], we’re on cooldown.
        self.next_state_allowed = {}  
        # Example: self.next_state_allowed["GetLightsState"] = 1697000000.0

    def set_usage_plan(self, plan):
        """
        plan is a dict of { skill_name: { 'lambdafunc': callable } }
        where 'condition' is a function that returns True if conditions are met.
        """
        self.usagePlan = plan

    def enqueue_prioritized_state(self, priority, new_state):
        """
        Add a new state to the priority heap with the given priority.
        Ensures no duplicate states of the same class exist in the queue.

        If 'priority' is lower than anything else in the queue,
        AND the state is not on cooldown,
        immediately switch to 'new_state' instead of waiting.
        """
        # 1) Do not add the same State class again if it's already in the queue
        new_state_class = new_state.__class__
        for scheduled in self.state_heap:
            if isinstance(scheduled.state_obj, new_state_class):
                # Allow multiple GearBuyingStates if their target shops differ.
                if new_state_class == GearBuyingState:
                    # Compare the target shop keys (stored in chosen_mobhash).
                    if scheduled.state_obj.chosen_mobhash == new_state.chosen_mobhash:
                        # Already enqueued a GearBuyingState for the same shop.
                        return
                else:
                    # For all other states, disallow duplicates.
                    return

        scheduled = ScheduledState(priority, new_state)
        new_state_name = new_state_class.__name__

        # 2) Check cooldown
        now = time.time()
        next_allowed_time = self.next_state_allowed.get(new_state_name, 0)

        # 3) If there's at least one state in the heap, see if we can "cut in line"
        if self.state_heap:
            # Because this is a min-heap, index 0 has the *lowest* numeric priority
            lowest_priority_in_heap = self.state_heap[0].priority

            # If 'priority' is strictly lower (i.e., higher urgency),
            # and our cooldown is expired, we can immediately switch.
            if priority < lowest_priority_in_heap and now >= next_allowed_time:
                # Immediately exit the current state
                self.state.exit(self)
                # Switch to the new state
                self.state = new_state
                self.state.enter(self)
                return

        # 4) Otherwise, just push it onto the heap as usual
        heapq.heappush(self.state_heap, scheduled)
        print(f"Enqueued {new_state_name} with priority {priority}")



    def change_state(self, new_state):
        response = None
        #if self.state is not new_state:
        #if not isinstance(new_state, self.__class__):

        # 1) Check if we’re currently on cooldown for new_state
        #new_state_name = new_state.__class__.__name__
        #old_state_name = self.state.__class__.__name__
        new_state_name = new_state.get_state_key() if hasattr(new_state, "get_state_key") else new_state.__class__.__name__
        old_state_name = self.state.get_state_key() if hasattr(self.state, "get_state_key") else self.state.__class__.__name__

        if new_state_name == old_state_name:
            return

        now = time.time()
        next_allowed_time = self.next_state_allowed.get(new_state_name, 0)

        print("Attempting state change to " + new_state_name)

        if now < next_allowed_time:
            # We are still within the cooldown for that state
            cooldown_remaining = next_allowed_time - now
            print(f"Cannot enter {new_state_name}; on cooldown for {cooldown_remaining:.1f} more seconds.")
            return None # abort the state change

        # 2) Exit the current state
        #if type(new_state) is not type(self.state):
        self.state.exit(self)
        #print("Exited old state")
        self.state = new_state
        response = self.state.enter(self)
        #print("Entered new state")
        #else:
        #    print("Somehow, we're making a state change between two states that are the same???")

        return response

    def queue_state(self, new_state):
        """
        Instead of changing state right away, we add it to our queue. 
        We'll pick it up in update() if we aren't in the middle of another state 
        or if the current state ends.
        """
        self.state_queue.append(new_state)

    def report_state(self):
        return self.state.__class__.__name__

    def set_state_cooldown(self, state_key_or_class, duration):
        """
        Sets a cooldown for 'duration' seconds on the given state.
        If state_key_or_class is a string, use it directly.
        Otherwise, if the object has a get_state_key() method, use that.
        Otherwise, use the class name.
        """
        if isinstance(state_key_or_class, str):
            state_key = state_key_or_class
        elif hasattr(state_key_or_class, "get_state_key"):
            state_key = state_key_or_class.get_state_key()
        else:
            state_key = state_key_or_class.__name__
        self.next_state_allowed[state_key] = time.time() + duration



    def update(self):
        """
        Called each "tick" (or each loop iteration).
        Let the current state execute. If that state finishes
        or explicitly says "I'm done", handle priority queue states or transition to NoState.
        """
        response = False #we need to report back to the main thread with output text

        # Let current state do its thing
        response = self.state.execute(self)

        # If the current state is done or it's a neutral state, process the queue
        if self.state.is_done or isinstance(self.state, NoState):
        #if self.state.is_done:
            skipped_states = []  # Temporarily hold skipped states
            all_on_cooldown = True  # Assume all states are on cooldown until proven otherwise

            message_accumulator = []
            while self.state_heap:
                # Peek at the highest-priority state
                next_scheduled = self.state_heap[0]
                next_state = next_scheduled.state_obj
                new_state_name = next_state.__class__.__name__

                # Check if the next state is on cooldown
                now = time.time()
                next_allowed_time = self.next_state_allowed.get(new_state_name, 0)

                if now < next_allowed_time:
                    # State is on cooldown; move it to a temporary list
                    cooldown_remaining = next_allowed_time - now
                    message_accumulator.append(f"{new_state_name} for {cooldown_remaining:.1f}s,")
                    skipped_states.append(heapq.heappop(self.state_heap))
                else:
                    # Found a valid state; transition to it
                    all_on_cooldown = False
                    self.change_state(next_state)
                    heapq.heappop(self.state_heap)  # Remove the executed state
                    break

            if message_accumulator:
                print( "Cooldowns: " + " ".join(message_accumulator) )

            # Restore skipped states to the heap
            for state in skipped_states:
                heapq.heappush(self.state_heap, state)

            # If all states are on cooldown, transition to NoState
            if all_on_cooldown:
                #print("All states are on cooldown. Switching to NoState and entering non-state action logic.")
                self.change_state(NoState())

        return response




    def print_state_heap(self):
        """
        Prints the contents of the state_heap with details about each state.
        """
        if not self.state_heap:
            print("State heap is empty.")
        else:
            print("Current state_heap contents (priority, state_name):")
            for item in self.state_heap:
                print(f"Priority: {item.priority}, State: {item.state_obj.__class__.__name__}")


def save_state():
    global internal_state
    global core_personality

    """Save the internal state to a file."""
    internal_state['qTable'] = qTable.copy()
    internal_state['mobTable'] = mobTable.copy()
    internal_state['memTable'] = memTable.copy()
    internal_state['shopTable'] = shopTable.copy()
    internal_state['pracTable'] = pracTable.copy()
    internal_state['generalmemory'] = generalmemory
    internal_state['recentbuffer'] = recentbuffer
    internal_state['commandmemory'] = commandmemory.copy()
    internal_state['actions'] = actions
    internal_state['futility'] = futility
    internal_state['score'] = score
    internal_state['recall_room'] = apparentrecallroom
    internal_state['npc_cooldowns'] = finitestate.next_state_allowed
    with open(core_personality['files']['state'], 'wb') as f:
        pickle.dump(internal_state, f)


def hash_but_doesnt_suck(s: str) -> str:
    """
    Returns a stable hash for a string using SHA-256.
    """
    digest_str = hashlib.sha256(s.encode('utf-8')).hexdigest()
    return int(digest_str, 16)


def hasher(string):
    thishash = hash_but_doesnt_suck(string)
    with open('loclog.txt', 'a') as file:
        file.write(string)
        file.write(str(thishash))
        file.write("\n\n")
    return thishash


#strip leading and trailing whitespace, then nuke control codes with isprintable
def strip_unprintable(s: str) -> str:
    ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
    s2 = ANSI_ESCAPE_RE.sub('', s)

    s3 = ''.join(ch for ch in s2 if ch.isprintable())
    s4 = s3.strip()
    print(s4)
    return s4


def validate_lambda(code):
    """
    A much more permissive validator that allows arbitrary lambda expressions
    with minimal checks. NOTE: This is potentially unsafe in production, as it
    allows arbitrary Python code execution. Use with caution.
    """
    print("validating lambda")
    print(code)

    # Parse the code into an AST, expecting an expression
    tree = ast.parse(code, mode='eval')

    # Ensure the root node is an Expression with a Lambda body
    if not isinstance(tree.body, ast.Lambda):
        raise ValueError("Code must be a lambda expression")

    # No further AST checks: we compile and return it directly
    return eval(compile(tree, filename="<ast>", mode="eval"))


def call_llm(messages, TOKEN_MAXIMUM=20):
    client = OpenAI()

    try:
        completion = client.chat.completions.create(
          model="gpt-4o-mini",
          #model="gpt-4o-2024-08-06",
          max_tokens=TOKEN_MAXIMUM,
          temperature=1.0,
          messages=messages
        )                     

        thiscompletion = completion.choices[0].message.content #OpenAI format
    except anthropic.RateLimitError as e: #Occurs when Anthropic throttles us
        print("Rate-limited by Anthropic!")
        thiscompletion = "YES"    

    return thiscompletion


def get_current_location(tn, input_text=None,allownew=False):
    returnval = 0

    if input_text == None:
        response = send_command(tn, "look")
    else:
        response = input_text

    print(response)
    match = re.search(r'37&8<<<\[ (.*?) \]>>>', response) #identify prompt and name of the room
    if match:
        regex = r'(?:.*37&6)?(' + re.escape(match.group(1)) + r'.*?)' + re.escape('(] Exits:')
        fullmatch = re.search(regex, response, re.DOTALL)
        if fullmatch:
            returnval = hasher(strip_unprintable(fullmatch.group(1)))
            print("Hash of currentloc: " + str(returnval))
    if allownew or returnval in qTable:
        return returnval,response
    else:
        #NO! This could cause the recall room to get overwritten. Maybe.
        #return apparentrecallroom[1] #apparentrecallroom's hash - will this ever even happen? Maybe 0 passes through.
        return 0,0

def add_loc_to_qtable(currentloc):
    if currentloc not in qTable:
        qTable[currentloc] = pd.Series([(0.0, np.int64(0)) for _ in actionspace], index=actionspace, name=currentloc)
        qTable[currentloc]["brain"] = (1.0, 0)
        qTable[currentloc]["recall"] = apparentrecallroom #With -100 weight, AI should never choose it, maing it Dijkstra-only.

def update_recall_edges(new_recall_target):
    global apparentrecallroom

    print("Updating recall edges to" + str(new_recall_target))
    apparentrecallroom = (-100, new_recall_target)
    for room_id, series in qTable.items():
        # Check if "recall" exists in the series; if not, add it
        if "recall" in series.index:
            # Update the recall direction to point to the new target
            qTable[room_id].at["recall"] = apparentrecallroom  # Example weight: 2.0

def signal_handler(signum, frame):
    """Handle the interrupt signal by saving the state and exiting."""
    print("Signal received, saving state...")
    save_state()
    sys.exit(0)

# Define the softmax function
def softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()

def extract_score(input_string):
    # Regular expression to find numbers enclosed in square brackets
    match = re.search(r'37&8<<<(.*?)>>>', input_string)
    if match:
        # Check if the string matches any of the elements
        if match.group(1) in roomsseen:
            return 0
        else:
            currentloc = match.group(1)
            roomsseen.add(currentloc)
            return 1 #score 1 point!

    else:
        # Return None if no number is found
        return 0

def append_recentbuffer(thistext):
    global recentbuffer
    global core_personality

    client = OpenAI()

    thistext = re.sub(r'\x1b\[.*?n', '', thistext) #purge escape sequences  
    thistext = re.sub(r'37&8.*?37&6', '', thistext, flags=re.DOTALL) #purge prompts. re.DOTALL allows multiline

    recentbuffer = recentbuffer + thistext

    if len(recentbuffer) > RECENTBUF_MAX_LEN:

        #print("Applying summary to recentbuf.")
        lines = recentbuffer.splitlines()
        summarize_us = '\n'.join(lines[:RECENTBUF_HEAD_LINES])
        dont_summarize_us = '\n'.join(lines[RECENTBUF_HEAD_LINES:])
        recentbuffer = dont_summarize_us

    with open(core_personality['files']['buffer'], 'w') as f:
        f.write(recentbuffer)

def read_until_prompt(tn, prompt='> ', timeout=TIMEOUT):
    """
    Read until a specific prompt is found.
    """
    global global_response
    global_response = tn.read_until(prompt.encode('ascii'), timeout=timeout).decode('ascii')
    return global_response

def send_command(tn, command, prompt='> \n'):
    """
    Send a command to the MUD server and print the response.
    """
    global actions
    global recentbuffer

    # Normalize the string to remove non-ASCII characters. Sometimes the LLM will try to send invalid chars.
    normalized_command = unicodedata.normalize('NFKD', command).encode('ascii', 'ignore').decode('ascii')

    append_recentbuffer( "> " + normalized_command + "\n" )

    tn.write(normalized_command.encode('ascii') + b"\n")
    actions += 1

    response = read_until_prompt(tn, prompt)
    if response != b'': #magic return value that is an "empty byte"
        append_recentbuffer( response )

    return response

# Convert DataFrame to Graph
def df_to_graph(df):
    graph = {}
    for col in df.columns:
        # Filter out NaN values and 0 values explicitly
        connected_nodes = df[col][(df[col] != 0) & (~df[col].isna())].tolist()        
        #connected_nodes = df[col][df[col] != 0].tolist()
        graph[col] = {node: 1 for node in connected_nodes if node != col}  # Assuming distance is 1
    return graph


def qtable_to_graph(dict):
    graph = {}
    for key, series in dict.items():
        graph[key] = {}
        for index, value in series.items():
            if value[1] != 0:
                graph[key][value[1]] = 1  # Assuming distance is 1
    return graph


#Dijkstra's Algorithm
def dijkstra(graph, start):
    # Initialize the shortest paths to infinity
    shortest_paths = {vertex: float('infinity') for vertex in graph}
    shortest_paths[start] = 0

    # Initialize the previous nodes to None
    previous_nodes = {vertex: None for vertex in graph}

    # Use a priority queue to keep track of the nodes to explore
    priority_queue = [(0, start)]

    while priority_queue:
        current_distance, current_node = heapq.heappop(priority_queue)


        #can this even happen?
        if current_node not in shortest_paths: 
            print("Current node not in shortest paths?")
            print(current_node)
            continue

        # If the current distance is greater than the recorded shortest path, skip
        if current_distance > shortest_paths[current_node]:
            continue

        #can this even happen?
        if current_node not in graph:
            print("Current node not in graph?")
            print(current_node)
            continue

        # Explore the neighbors of the current node
        for neighbor, weight in graph[current_node].items():
            #Sometimes neighbor isn't in shortest_paths. I don't know how this happens.
            if neighbor not in shortest_paths:
                shortest_paths[neighbor] = float('inf') #Stopgap solution
                print(f"Warning: Missing node {neighbor} in shortest_paths. Setting it to inf.")

            distance = current_distance + weight

            # Only consider this new path if it's shorter
            if distance < shortest_paths[neighbor]:
                shortest_paths[neighbor] = distance
                previous_nodes[neighbor] = current_node
                heapq.heappush(priority_queue, (distance, neighbor))

    return shortest_paths, previous_nodes



#Claude 3.5 Sonnet AI call
def use_big_brain(tn, initialmessages, currentloc, exiting, fighting):
    global generalmemory
    global recentbuffer
    global memTable
    global commandmemory
    global summarize_counter
    global actions
    global braincooldown
    newdiscovery = False
    wemoved = False
    response = ""

    #client = anthropic.Anthropic()
    client = OpenAI()

    braincooldown = actions #the action counter is an effective timer. remember that use_big_brain consumes actions too!

    messages = initialmessages


    if currentloc in commandmemory:
        #print("Appending command history:\n")
        for thisoldcommand in commandmemory[currentloc]:
            messages.append(thisoldcommand)

    thesecommands = ""

    #generalmemory is miserable and counterproductive. Don't know why. Disabling.
    if exiting:
        comboprompt = preprompt_exit + "\n\n" + thesecommands    
    elif fighting:
        comboprompt = preprompt_combat
    else:
        comboprompt = preprompt + "\n"
        if currentloc in memTable:
            comboprompt += memoryprompt
            for thismemory in memTable[currentloc]:
                comboprompt += thismemory + "\n"
        comboprompt += thesecommands

    #Claude models take a "system" var, whereas OpenAI models require a "system" role in the messages object.
    #For OpenAI, we'll prepend our messages with the system here.
    system_message = {"role": "system", "content": comboprompt}
    messages.insert(0, system_message)

    #print(messages)

    oldloc = currentloc
    commandsequence = ""
    for x in range(MAXAICOMMANDS):
        try:
            completion = client.chat.completions.create(
              #model="gpt-3.5-turbo",
              model="gpt-4o-mini",
              max_tokens=200,
              #temperature=1.0,
              temperature=min(MAXIMUM_TEMP, MINIMUM_TEMP + (futility/FUTILITYRECALLTHRESHOLD)*(MAXIMUM_TEMP-MINIMUM_TEMP)),
              messages=messages
            )             
            #thiscompletion = completion.content[0].text #Anthropic format
            thiscompletion = completion.choices[0].message.content #OpenAI format
        except (anthropic.RateLimitError, anthropic.BadRequestError) as e: #Occurs when Anthropic throttles us
            print("Rate-limited by Anthropic!")
            thiscompletion = "Nope"
            return response
            break

        regex = r'(.*?)' + re.escape('37&8<<<') #possible shenanigans if we're not ungreedy here

        thiscommand = thiscompletion
        commandsequence = commandsequence + thiscommand + ";"

        print(thiscommand)
        response = send_command(tn, thiscommand)
        response = strip_ansi(response)
        print(response)
        thisoutput = response
        try:
            fullmatch = re.search(regex, response, re.DOTALL)
            thisoutput = fullmatch.group(1)
        except AttributeError as e:
            print("Some sort of parsing error!")
        print(thisoutput)

        appendassistant = {"role": "assistant", "content": thiscommand}
        appenduser = {"role": "user", "content": thisoutput+postprompt2 }
        messages.append(appendassistant)
        messages.append(appenduser)


        oldloc = currentloc
        match = re.search(r'37&8<<<\[ (.*?) \]>>>.*37&0', response) #identify prompt and name of the room
        if match:
            regex = r'(' + re.escape(match.group(1)) + r'.*?)' + re.escape('(] Exits:') #possible shenanigans if we're not ungreedy here
            fullmatch = re.search(regex, response, re.DOTALL)
            if fullmatch:
                #print("Hashing this:")
                #print(fullmatch.group(1))
                currentloc = hasher(strip_unprintable(fullmatch.group(1)))
                print(currentloc)


        #add this command to the command memory
        if currentloc not in commandmemory:
            commandmemory[currentloc] = [] #is this syntax correct?

        commandmemory[currentloc].append(appendassistant) 
        commandmemory[currentloc].append(appenduser) 
        if len(commandmemory[currentloc]) > 10:
            commandmemory[currentloc] = (commandmemory[currentloc])[-10:]


        if oldloc != currentloc: #did we go somewhere?
            wemoved = True
            print("WE MOVED!") #We're getting a lot of spurious movement claims when no movement actually happens. Why?
            commandmemory[currentloc] = [] #Can clear it - gets contaminated otherwise. The useful commands get saved to qTable anyway.
            #make a new action on oldloc's qTable that encodes the actions taken by the brain
            print("Command sequence: " + commandsequence)
            if oldloc in qTable and commandsequence not in qTable[oldloc].index: #is this the first time we've done this?
                print("New command sequence for this room!")

                #but, is this redundant with an existing movement?
                #if not (qTable[oldloc] == currentloc).any():
                if not qTable[oldloc].apply(lambda x: x[1] == currentloc).any():
                    print("This is a novel destination!")
                    newdiscovery = True
                    print(qTable[oldloc])
                    qTable[oldloc] = qTable[oldloc].reindex(qTable[oldloc].index.append(pd.Index([commandsequence])), fill_value=(2.0,currentloc))
                    print(qTable[oldloc])
                    #qTable[oldloc][commandsequence] += 2.0
                    #print(qTable[oldloc])
                else:
                    print("This is a redundant destination.")

            break

    #this is a very stupid way to not have "YOUR COMMAND?" show up at the end of the prompt
    messages.pop() #remove the last message (thisoutput+postprompt)

    if newdiscovery:
        messages.append({"role": "user", "content": learningprompt_newdiscovery }) #praise LLM for success
    elif wemoved:
        messages.append({"role": "user", "content": learningprompt }) #cut off thisoutput, which will be an rdesc
    else:
        messages.append({"role": "user", "content": thisoutput+learningprompt }) #this is fine

    #Claude models take a "system" var, whereas OpenAI models require a "system" role in the messages object.
    #For OpenAI, we'll prepend our messages with the system here.
    #system_message = {"role": "system", "content": preprompt}
    #messages.insert(0, system_message)

    #print("LEARNING PROMPT:")
    #print(messages)

    try:
        completion = client.chat.completions.create(
          #model="gpt-3.5-turbo",
          model="gpt-4o-mini",
          max_tokens=200,
          temperature=1.0,
          messages=messages
        )                     

        #thiscompletion = completion.content[0].text #Anthropic format
        thiscompletion = completion.choices[0].message.content #OpenAI format
    except anthropic.RateLimitError as e: #Occurs when Anthropic throttles us
        print("Rate-limited by Anthropic!")
        return response
        thiscompletion = "Nope"

    #if wemoved:
    #    print("We moved, so SUPPRESSING MEMORY FORMATION.")
    #else:
    print("WE LEARNED:")
    print(thiscompletion)

    if oldloc not in memTable:
        memTable[oldloc] = []
    (memTable[oldloc]).append(thiscompletion)

    if len(memTable[oldloc]) >= MEMORYPRUNING_PERROOM:
        print("Pruning.")
        memTable[oldloc] = memTable[oldloc][-MEMORYPRUNING_PERROOM:] #Pruning. Should help prevent runaway wrong solutions.

    #print("MEMORIES FOR THIS ROOM:\n")
    #for oldmemory in memTable[oldloc]:
    #    print(oldmemory)



    total_memories = sum(len(lst) for lst in memTable.values())
    #print("total memories is ")
    #print(total_memories)


    if (total_memories % MAKENEWMEMORIES) == 0: #turn room-specific memories into general memories, periodically
        thesememories = ""
        # Iterate over all list elements in the dictionary
        for key, value_list in memTable.items():
            #print(f"Elements in list for key '{key}':")

            for thismemory in value_list:
                print(thismemory)
                thesememories += thismemory

            #We have to stop if there's too much.
            if len(thesememories) > MAXIMUM_MEMORY_PROMPT:
                print("We maxed out on memory length!")
                break                

        messages=[
            {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": summarizeprompt+thesememories
                        }
                    ]
            }
        ]
        try:
            completion = client.chat.completions.create(
              #model="gpt-3.5-turbo",
              model="gpt-4o-mini",
              max_tokens=250,
              temperature=1.0,
              messages=messages
            ) 
        except RateLimitError as e: #Occurs when Anthropic throttles us
            print("Rate-limited by Anthropic!")
            thiscompletion = "Nope"
        #thiscompletion = completion.content[0].text #Anthropic format
        thiscompletion = completion.choices[0].message.content #OpenAI format

        print(thiscompletion)
        if len(thiscompletion) < 450: #just in case
            generalmemory = thiscompletion
        else:
            print("General memory too long!")

    #Checking to see if we moved or changed exit status (like opening a door)
    command = "look"
    response = send_command(tn, command)
    response = strip_ansi(response)

    return response




# FLOW STARTS HERE
def main():
    global score
    global generalmemory
    global recentbuffer
    global actions
    global qTable
    global mobTable
    global memTable
    global shopTable
    global pracTable
    global futility
    global internal_state
    global braincooldown
    global rp_storyline
    global core_personality
    global apparentrecallroom
    global finitestate;

    #try:


    parser = argparse.ArgumentParser(description="Set the system state to A, B, or C")
    # Add the argument for systemMode
    parser.add_argument(
        'systemMode',
        nargs='?', # This makes the argument optional
        choices=['rp', 'explore', 'leveling', 'general', 'equip'],
        default = 'general',
        help='The system mode to set (general, rp, explore, or leveling)'
    )    
    # Add the argument for charFile
    parser.add_argument(
        'charFile',
        nargs='?',  # This makes the argument optional
        default='danmccay.yaml',
        help='The character file to load (optional)'
    )

    # Parse the arguments
    args = parser.parse_args()
    
    # Access the systemMode argument
    system_mode = args.systemMode
    personality_file = args.charFile

    print(f"Mode set to: {system_mode}")

    with open(personality_file, 'r') as file:
        core_personality = yaml.safe_load(file)


    currentloc = 0
    dirToGo = actionspace[0] #sure, why not
    noexit_timeout = TIMEOUT
    use_brain_for_nextloop = False


    # Connect to the MUD server
    tn = telnetlib.Telnet(HOST, PORT)
    
    generalmemory = "You're new at this MUD, and you haven't learned any general strategies yet."

    qTable[currentloc] = pd.Series([(0.0, np.int64(0)) for _ in actionspace], index=actionspace, name=currentloc)

    print(qTable)

    finitestate = NPC(core_personality['finitestate']['name'], tn) #finite state machine for combat

    last_rp_time = time.time()
    rp_state = False

    # Wait for the login prompt or initial screen
    initial_screen = tn.read_until(b"login:", TIMEOUT).decode('ascii')
    print(initial_screen)

    # Example: Send a login command. Adjust this to fit the MUD's login procedure.
    # You might need to handle more interactions, like password input.

    username = core_personality['playerfile']['name']
    password = core_personality['playerfile']['pwd']
    response = send_command(tn, username)
    print(response)
    response = send_command(tn, password)
    print(response)

    # Example: Send another command after logging in. Adjust as necessary.
    command = "y"  # Maybe we need to say "yes" to reconnect; this will also get us past the MOTD
    response = send_command(tn, command)
    print(response)

    actions = 0 #total number of turns taken
    futility = 0 #how many movements have passed since we last discovered a room OR fought an enemy, depending on priorities
    thisrpsleep = 0
    futilitythreshold = 6
    path = []
    multicommand = []

    # Register the signal handler for SIGINT (Ctrl-C)
    signal.signal(signal.SIGINT, signal_handler)

    state_file = core_personality['files']['state']

    # Load the state if the state file exists
    try:
        with open(state_file, 'rb') as f:
            internal_state = pickle.load(f)
            #print(internal_state)
            qTable = internal_state['qTable'].copy()
            #print("SAVED QTABLE")
            #print(qTable)
            mobTable = internal_state['mobTable'].copy()
            memTable = internal_state['memTable'].copy()
            shopTable = internal_state['shopTable'].copy()
            pracTable = internal_state['pracTable'].copy()
            generalmemory = internal_state['generalmemory']
            recentbuffer = internal_state['recentbuffer']
            commandmemory = internal_state['commandmemory'].copy()
            actions = internal_state['actions']
            futility = internal_state['futility']
            score = internal_state['score']            
            apparentrecallroom = internal_state['recall_room']

            #an 'if' for now because of versioning
            if 'npc_cooldowns' in internal_state:
                finitestate.next_state_allowed = internal_state['npc_cooldowns']            

            print("State loaded, probably.")
    except FileNotFoundError:
        print("Couldn't find state file.")
        pass


    graphfile = core_personality['files']['graph']
    graph = qtable_to_graph(qTable)
    # Specify the output file path
    # Save the data to the file
    with open(graphfile, 'w') as f:
        json.dump(graph, f, indent=4)        
    # Confirmation message
    print("Wrote latest graph to " + graphfile)

    print("Hello.")


    currentloc = get_current_location(tn)[0]
    add_loc_to_qtable(currentloc)
    print("We're starting in " + str(currentloc))
    start_time = time.time()

    client = OpenAI()

    lambdacode = """
lambda chardata: (
    chardata.combatround > 0
    and any('shield' in value for value in chardata.equipment.keys())
    and chardata.health > 50
)
"""

    usage_plan = {}

    # Validate and compile the condition
    condition = validate_lambda(lambdacode)

    # Wrap the condition with a try/except block
    def safe_condition(c, original_condition=condition):
        try:
            return original_condition(c)
            print(c.equipment.values())
        except Exception as e:
            print(f"Runtime error in condition for. {e}")
            return False  # Safe fallback if the lambda raises an error

    usage_plan["slam"] = { "lambdafunc": safe_condition }

    finitestate.set_usage_plan(usage_plan)

    try:
        while True:
            if actions % SAVE_INTERVAL == 0 and actions > 0:
                print("Periodically saving state...")
                save_state()
                
            print("\nSCORE: " + str(score) + " ACTIONS: " + str(actions) + " FUTILITY: " + str(futility) + " COMBATR: " + str(finitestate.combatround) + " MODE: " + str(system_mode))

            #time.sleep(TIMEOUT)
            time.sleep( max(0, TIMEOUT - (time.time()-start_time)) )
            start_time = time.time()

            #flush output queue
            outqueue = tn.read_very_eager()
            if outqueue != b'': #magic return value that is an "empty byte"
                print(outqueue)
                if response == None: #This should never happen, but sometimes it does
                    print("Response was none! Setting response to outqueue.")
                    response = outqueue
                append_recentbuffer(outqueue.decode('ascii'))
                
            print(response)
            #statecheck = re.search(r'.*37&7HP:(\d+)\% MP:(\d+) MV:(\d+) Money:(\d+) Lv:(\d+) TNL:(\d+) 37&6', response)

            #37&7HP:100% MP:112 MV:120 Money:79k Lv:11 TNL:1873 37&6 
            #This code pattern returns the last match in response, as opposed to re.search's first match.
            statecheck = None
            for iterate in re.finditer(r'.*37&7HP:(\d+)\% MP:(\d+) MV:(\d+) Money:(\d+).*Lv:(\d+) TNL:(\d+) 37&6', response):
                statecheck = iterate

            if statecheck:
                finitestate.health = int(statecheck.group(1))
                finitestate.mana = int(statecheck.group(2))
                finitestate.move = int(statecheck.group(3))
                finitestate.money = int(statecheck.group(4))

                oldlevel = finitestate.level
                finitestate.level = int(statecheck.group(5))

                #Have we recently leveled up?
                if oldlevel > 1 and finitestate.level > oldlevel:
                    print("We leveled up! Turning off bloodthirstiness.");
                    system_mode = args.systemMode #whatever we were at the start

                finitestate.tnl = int(statecheck.group(6))
                if "HUNGER" in statecheck.group(0):
                    finitestate.hunger = True
                else:
                    finitestate.hunger = False
                if "THIRST" in statecheck.group(0):
                    finitestate.thirst = True
                else:
                    finitestate.thirst = False

            #Are we in combat?
            #copying prompt below as a reminder
            #prompt 37&8<<<%r>>>37&9<<<%e>>>37&0|%o|%c%u%U37&7HP:%y MP:%m MV:%v Money:%w Lv:%l TNL:%X 37&6
            #prompt 37&8<<<%r>>>37&9<<<%e>>>37&0|%o|%u%U37&7HP:%y MP:%m MV:%v Money:%w Lv:%l TNL:%X 37&6
            ourstate = finitestate.report_state()

            combatcheck = re.search(r'37&0\|(.+?)\|', response)
            if combatcheck:
                combatcheck2 = re.search(r'(\d+)\%', combatcheck.group(1))
                if combatcheck2:
                    finitestate.combatround += 1
                    #print("In combat!" "Round " + str(finitestate.combatround))
                    finitestate.enqueue_prioritized_state(5,CombatNeutralState())
                    finitestate.opphealth = int(combatcheck2.group(1))
                else:
                    #print("Not in combat!")
                    finitestate.combatround = 0
            else:
                print("No prompt found. This should be considered a bug.")

            #response = finitestate.update()
            mayberesponse = finitestate.update()
            #print("mayberesponse? " + str(mayberesponse))
            if mayberesponse is not None and mayberesponse is not False:
                response = mayberesponse


            if finitestate.combatround > 0:
                continue

            if finitestate.thirst == True:
                print("I'm thirsty.")
                finitestate.enqueue_prioritized_state(20,QuenchingState())
            if finitestate.hunger == True:
                print("I'm hungry.")
                finitestate.enqueue_prioritized_state(20,FeastingState())

            if finitestate.report_state() != "NoState":
                continue

            #print("Entering non-state action logic")

            #special cases - do we need to get up?             
            if "Better stand up first." in response:
                response = send_command(tn, "stand")
                print(response)
                continue
            elif "In your dreams, or what?" in response:
                response = send_command(tn, "stand")
                print(response)
                continue
            elif "Maybe you should finish fighting first?" in response:
                #No obvious action to take if in combat, but aborting here at least stops us from updating our weights inappropriately
                print(response)
                continue;
            elif "You are too exhausted." in response:
                response = send_command(tn, "rest")
                print(response)
                time.sleep(RESTTIME)
                continue
            
            match = re.search(r'37&8<<<\[ (.*?) \]>>>37&9', response) #identify prompt and name of the room

            if not match: #somehow, no prompt?
                print("No match?? Bailing out.")
                continue #bail out

            regex = r'(' + re.escape(match.group(1)) + r'.*?)' + re.escape('(] Exits:') #possible shenanigans if we're not ungreedy here
            fullmatch = re.search(regex, response, re.DOTALL)


        # Close the connection
        tn.close()
    except KeyboardInterrupt:
        # Save state on any other exception
        print(f"Exception occurred: {e}, saving state...")
        save_state()
        sys.exit(1)        

if __name__ == "__main__":
    main()