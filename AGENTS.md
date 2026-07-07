# AGENTS.md

## Core Principles

- **DESIGN AND BUILD PRODUCTION GRADE CODE FROM THE START** - i.e. no monolithic files when they can modularised, no unnecessary duplication of code, no hardcoded values when they can be placed in a centralised config file, etc.
- Keep AGENTS.md minimal - **only** keep information which will be required every time you look at this project. Do not modify `Core Principles`, but review and remove anything else from the document which is not required.
- You **must** review your own code as you write as a *Senior Software Engineer*.
- Write the bare minimum of tests - follow **ARRANGE, ACT, ASSERT**. You must test the end result, NOT the implmentation details.
- If you start the application (e.g. for testing), you must validate that the application is stopped before marking the task complete.
- Use mocks for testing sparingly - if the code requires excessive mocking, then redesign the implementation to be easier to test.
