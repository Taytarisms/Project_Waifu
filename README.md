# Project Waifu is meant to bring building an AI VTuber or "w-AI-fu" to life with customizations you can make for yourself in a dedicated UI!



## Walkthroughs
I will to the best of my abilities create mini-README's so that you can set-up the program as *you* need it
as well as setting up some more complicated things like the VTubeStudio integration or Multi-Platform setups.

## Overview
Project Waifu is a project made for people to use as an on-stream companion or personal use offline companion!
- Host your own AI Vtuber stream. The AI can respond to stream chat on up to three different platforms with integrations in place for Twitch, YouTube and Kick!
- Have your companion hear you directly from your mic or see your screen/images inside the UI.
- Have a one-on-one converstaion with an AI Vtuber, whose personality and character description can be customized to your liking.
- You can run it completely online through API's or entirely Local on your PC.
- The best part? You can actually blend the two if you like - giving YOU as the end-user the power to build it into something your own!

## Installation
I have intentionally shipped this with an embedded Python (3.13) so you don't have to install it and include other things like ffmpeg/ffmprobe for audio playback.

```
1. Extract the downloaded zip file to wherever you want to keep it. (Desktop, Downloads, External Drive, etc.)

2. Run the "setup" file inside the directory and it will install the program and everything necessary to run it.
2a. If for some reason that the build fails, you can copy the "setup_log" file and paste it into an "Issues" claim and I'll review it!

3. Once downloaded and it gives you the go-ahead to close the set-up, run it for the first time and ensure the UI builds successfully.
3a. From there, you'll get to decide what you want to set-up in terms of your companion! Have fun!

```

## Links
- I've got a discord server where you can try this tool with other people, troubleshoot your issues and suggest features:
  https://discord.gg/5yqS3Hem4S
- I also stream occasional updates on this app and you can follow me here:
  https://www.twitch.tv/fellstartay

## Additional details
- You **MUST** get your own API keys for the online services you decide to use. I do have dashboard keys inside the respective pages that'll lead you to the appropriate place to set-up an account.
- You do NOT have to use VTube Studio and can use this app as a solely offline companion.
- When using the local LLM and TTS functionalities, ensure you have enough VRAM/RAM on your machine to handle it with enough overhead for you to do other things like gaming, streaming, etc.

# FAQ
- Q: How do I know which Local LLM is best for my machine?
- A: Depends on what GPU you have inside the "Browse HuggingFace" tab - I have included a metric to measure your GPU's available VRAM. Then the corresponding model cards will tell you if the model is compatible or not.

- Q: Do I need to worry about memories fading away?
- A: Memories will **NOT** fade unless you remove the "chroma_memories" folder inside the main memories folder.

 ### Speech to Text Input: "thank you, you, I'm gonna, etc." ### 
The transcriber often transcribes silence into "you". Make sure you selected the correct input device in settings and check the settings in the "STT" tab. Make sure you set everything correctly for your specs since there's no real "universal" set-up.

## Dedication

**I would like to give a massive shoutout to:
The entire AIVT Community as a whole for inspiring me to go ahead and create my own spin on making this project happen!**
