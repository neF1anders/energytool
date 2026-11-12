"""Application: use cases и порты. Оркестрирует domain, IO только через порты.

Use cases:
- handle_turn.HandleTurn              один ход веб-чата
- answer_question.AnswerQuestion      один вопрос этапа 1 (stateless)
- submit_lead.SubmitLead              заявка + бриф
- retrieve_knowledge.RetrieveKnowledge  поиск по базе: KnowledgeQuery -> KnowledgeResult
- build_index.BuildKnowledgeIndex     индексация базы на старте
"""
