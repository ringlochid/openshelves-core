   2. Data Integrity Risk: `linked_user_id` Not Validated
       * Severity: Medium
       * Location: routers/author.py
       * Description: When creating an author, the provided linked_user_id is not validated against the
         external Auth Service. This allows author profiles to be linked to non-existent user accounts, posing
         a data integrity risk. This is a known, deferred issue noted in the roadmap and in code comments.
       * Recommendation: As documented in the code, implement a check against the Auth Service to verify the
         user exists before linking them to an author profile.