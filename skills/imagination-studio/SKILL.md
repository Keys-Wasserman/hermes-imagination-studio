---
name: imagination-studio
description: How to make good pictures, clips, voice lines, songs and sound effects with the Imagination Studio tools (studio_*), and the rules that keep the machine safe.
---

# Making media with Imagination Studio

The `studio_*` tools send work to Imagination Studio on the person's own machine. The Studio renders one heavy job at
a time in its own queue, checks memory before it starts, and pauses GPU work until the machine has cooled. So:

1. **Start with `studio_status`.** It says which picture models, clip sizes, qualities and sound engines this Studio has,
   with their licences, and what is already queued. Use only what it lists.
2. **Queue, don't hammer.** Make several things by queueing them and letting the Studio pace them. A job that waits at
   "Cooling the GPU" or for memory is normal: never ask the person to turn the Studio's safety gates off.
3. **Long jobs come back later.** Clips and songs often take minutes. When a tool returns `status` queued or running,
   tell the person it is rendering and call `studio_job` with the `job_id` later (with `wait_seconds` up to 600).
4. **Deliver files.** When a result has `media_tag`, put its `MEDIA:` lines in your reply exactly as given, each on its
   own line. That is how the files reach Telegram, Discord or the Desktop app.
5. **Chain the steps.** A picture's Library path (from the job's `outputs`, e.g. `scouts/<job>/scout_s1.png`) is the
   `first_frame` of a clip. `studio_library` finds earlier work by what is in it.

## Prompts that work

- **Pictures:** subject, place, light, lens and style in one or two sentences ("a lighthouse keeper on a stormy cliff
  at dusk, 35 mm, warm lamp light against blue rain"). Say the text you want written in quotes.
- **Clips:** what happens, then the camera, then the sound ("she turns and walks into the rain; slow push in; rain
  and distant thunder"). Up to 15 s per clip.
- **Voice (Fish Audio Drama 3):** the voice and delivery in brackets, then the words: `[a calm older man, almost a
  whisper] Keep the lamp lit.` Describe a voice; never ask for a real person's voice.
- **Songs (MiniMax Music 3):** a caption with genre, tempo, mood, vocals and instruments, and lyrics with `[Verse]`,
  `[Chorus]`, `[Bridge]` tags. Write original lyrics; never reproduce a real song's words. No lyrics = instrumental.
- **Sound effects (Stable Audio Open):** concrete sounds, not music: "boots on wet gravel, slow steps, close".

## Licences

Each engine runs under its own licence, which `studio_status` shows (for example Fish Audio's community licence for
voice, the MiniMax-Music3 community licence, Stability AI's community licence). Mention it when the person plans to
use the result commercially.
