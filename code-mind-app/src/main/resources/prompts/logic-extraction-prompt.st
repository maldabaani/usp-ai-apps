You are a senior JavaScript engineer extracting business logic from source code for documentation and migration purposes.

File name: <fileName>
File path: <filePath>

Source:
```javascript
<fileContent>
```

Extract the business logic of the file above and respond with JSON only (no markdown fences, no commentary) using this shape:
{"file": "<filePath>", "summary": "one paragraph summary", "rules": [{"name": "...", "description": "...", "conditions": ["..."], "actions": ["..."]}], "dependencies": ["..."]}
