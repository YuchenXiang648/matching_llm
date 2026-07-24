---
name: stage3-email-draft
description: "Generate a formal first-contact email grounded in student profile and supervisor data."
---


# SKILL.md — Stage 3 Email Draft

## Purpose
Generate a formal first-contact email from the student to the selected supervisor.

## When to use
Use this skill after the student has explored a supervisor and wants help drafting the first email.

## Inputs
- student profile,
- Stage 1 summary,
- Stage 2 conversation summary,
- selected supervisor detailed profile.

## Output
Return a complete email draft with:
- subject line,
- greeting,
- short body paragraphs,
- polite closing,
- signature placeholders where needed.

## Core rules
1. The email must be professional, polite, and concise.
2. Use only supported information.
3. Never invent student achievements or supervisor facts.
4. If a detail is missing, use a clear placeholder such as `[Your Name]`.
5. Mention the student's interest in the supervisor specifically, based on the available data.
6. Briefly explain why the project or research area is appealing.
7. Keep the tone appropriate for a first contact with a professor.
8. Ask whether the supervisor is available to discuss supervision or project opportunities.

## Output style
- Formal but natural.
- Usually around 160 to 220 words.
- No fake attachment claims.
- No bullet points in the final email.
