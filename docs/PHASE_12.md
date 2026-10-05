# Phase 12: Llama 3.2:3b creative work workflows

Phase 12 connects `llama3.2:3b` to structured creative workflows while explicitly separating language creativity from image generation.

## Creative delegation

DeepSeek may delegate to Llama for:

- Creative concepts
- Branding
- Copywriting
- Visual direction
- Image prompts
- Presentation structure
- Creative revisions

Llama returns creative work to DeepSeek. It is not a user-facing chatbot and it does not replace DeepSeek's final review or presentation.

## Registered tools

- `creative_create_brief`
- `creative_write_copy`
- `creative_visual_direction`
- `creative_make_image_prompt`
- `creative_structure_presentation`
- `creative_generate_image`

The first five tools are language/structure workflows that Llama 3.2:3b can support. They return structured briefs, copy blocks, visual direction, image prompts, and slide outlines.

## Actual image generation

Llama 3.2:3b is a language model. It cannot generate image pixels by itself. `creative_generate_image` therefore requires an explicitly configured OpenAI-compatible image endpoint:

```dotenv
OPENMANUS_IMAGE_BASE_URL=https://your-image-service.example/v1
OPENMANUS_IMAGE_API_KEY=replace-me
OPENMANUS_IMAGE_MODEL=your-image-model
```

When configured, the service receives the reviewed prompt at `/images/generations`. When it is not configured, the tool fails honestly with a message explaining that Llama can produce the prompt but cannot generate the image alone.

The application never claims that Llama generated an image when only a prompt or visual direction was produced.

## Creative revision flow

```text
DeepSeek defines the creative objective
  ↓
Llama creates brief, copy, direction, prompt, or structure
  ↓
DeepSeek reviews and requests a revision if needed
  ↓
For actual imagery, an explicitly configured image service generates pixels
  ↓
DeepSeek presents the approved creative result
```
