# Project Waifu is meant to bring building an AI VTuber or "w-AI-fu" to life with customizations you can make for yourself in a dedicated UI!

## You can run it completely online through API's or entirely Local on your PC. The best part? You can actually blend the two if you like - giving YOU as the end-user the power to build it into something your own!

## Overview
Project Waifu is a project made for people to use as an on-stream companion or personal use offline companion!
- Host your own AI Vtuber stream. The AI can respond to stream chat with capabilities to react to stream events using Twitchio's Eventsubs.
- Hear you directly from your mic or see your screen/images inside the UI.
- Have a face-to-face converstaion with an AI Vtuber, whose personality and character description can be customized to your liking.
- Lastly, you can use this 

## Installation
I have intentionally shipped this with an embedded Python (3.13) so you don't have to install it and include other things like ffmpeg/ffmprobe for audio playback.
All you'll need to do is run the "setup.bat" file and let it install then run the "start_bot.bat" file and the app is yours to construct as you like!

## Usage
For info on how to use the program, see the full demo video: (insert final link here)

You can buy the audio cables here:

https://vb-audio.com/Cable/ (In your use case, you may only need two)

## Links
- I've got a discord server where you can try this tool with other people, troubleshoot your issues and suggest features:
https://discord.gg/5yqS3Hem4S
- I also stream occasional updates on this app and you can follow me here:
https://www.twitch.tv/fellstartay

## Additional details
- You **MUST** get your own API keys for the online services you decide to use. I do have dashboard keys inside the respective pages that'll lead you to the appropriate place to set-up an account.
- You do NOT have to use VTube Studio and can use this app as a solely offline companion.
- When downloading models from HuggingFace for Local LLM models, if it supports Vision, you **MUST** download the appropriate mmproj file (otherwise it will run ONLY in Text-Only mode):
-  <img width="467" height="203" alt="image" src="https://github.com/user-attachments/assets/485a21ae-7759-496a-952e-56970492590e" />


# FAQ
- Q: How do I know which Local LLM is best for my machine?
- A: Depends on what GPU you have inside the "Browse HuggingFace" tab - I have included a metric to measure your GPU's available VRAM. Then the corresponding model cards will tell you if the model is compatible or not.

- Q: Do I need to worry about memories fading away?
- A: Memories will **NOT** fade unless you remove the "chroma_memories" folder inside the main memories folder.

 ### Speech to Text Input: "thank you, you, I'm gonna, etc." ### 
The transcriber often transcribes silence into "you". Make sure you selected the correct input device in settings and check the settings in the "STT" tab. Make sure you set everything correctly.


I would like to give a massive shoutout to:
The entire AIVT Community as a whole for inspiring me to go ahead and create my own spin on making this project happen!
