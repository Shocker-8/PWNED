import json
from pathlib import Path
import tempfile
import unittest
from candidatelab.interview import Interview, LocalGemma, MODEL, conduct

class Agent:
    def __init__(self,*responses):self.responses=iter(responses);self.calls=[]
    def complete(self,payload):
        self.calls.append(payload)
        response = next(self.responses)
        return {"unknown_facts": [], **response}

class InterviewTests(unittest.TestCase):
    def test_initial_dump_extracts_known_fields_and_selects_relevant_followup(self):
        agent=Agent({'facts':[{'key':'name','value':'John','evidence':'John'},{'key':'occupation','value':'doctor','evidence':'doctor'}],'next_topic':'education'})
        session=Interview('pairs','owned')
        self.assertEqual(session.step('John is a doctor.',agent),'education')
        self.assertNotIn('name',session.available)
        self.assertNotIn('occupation',session.available)

    def test_repeated_topic_rejected_without_mutating_state(self):
        session=Interview('pairs','owned')
        session.step('John',Agent({'facts':[{'key':'name','value':'John','evidence':'John'}],'next_topic':'occupation'}))
        with self.assertRaisesRegex(ValueError,'answered'):
            session.step('engineer',Agent({'facts':[{'key':'occupation','value':'engineer','evidence':'engineer'}],'next_topic':'name'}),'occupation')
        self.assertNotIn('occupation',session.facts)
        self.assertEqual(session.rounds,1)

    def test_business_context_cannot_select_personal_biography_topic(self):
        session=Interview('wifi-business','owned')
        self.assertNotIn('education',session.available)
        with self.assertRaises(ValueError):
            session.step('restaurant',Agent({'facts':[],'next_topic':'occupation'}))
        self.assertEqual(session.step('Copper Moon restaurant',Agent({'facts':[{'key':'organization_type','value':'restaurant','evidence':'restaurant'}],'next_topic':'brand_words'})),'brand_words')

    def test_unquoted_and_invented_facts_rejected(self):
        for value,evidence in [('degree','doctor'),('London','London')]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                Interview('pairs','owned').step('doctor',Agent({'facts':[{'key':'education','value':value,'evidence':evidence}],'next_topic':None}))

    def test_conflicting_facts_not_overwritten(self):
        session=Interview('passwords','owned')
        session.step('John',Agent({'facts':[{'key':'name','value':'John','evidence':'John'}],'next_topic':None}))
        with self.assertRaisesRegex(ValueError,'conflicting'):
            session.step('Jane',Agent({'facts':[{'key':'name','value':'Jane','evidence':'Jane'}],'next_topic':None}))
        self.assertEqual(session.facts['name']['value'],'John')

    def test_unknown_is_not_reasked_and_fact_ledger_is_not_generation_config(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=Agent({'facts':[],'next_topic':'name'},{'facts':[],'next_topic':None})
            answers=iter(['Some context','','dunno'])
            session=conduct('pairs','owned',Path(folder)/'case.json',agent,lambda _:next(answers),lambda _:None)
            self.assertIn('name',session.unknown)
            self.assertNotIn('name',agent.calls[-1]['available_topics'])
            saved=json.loads((Path(folder)/'case.json').read_text())
            self.assertFalse(saved['generation_inputs_approved'])
            with self.assertRaises(FileExistsError):session.save(Path(folder)/'case.json')

    def test_username_interview_has_no_password_biography_questions(self):
        self.assertEqual(set(Interview('usernames','owned').topics),{'name','usernames','word_languages'})

    def test_budget_and_unsupported_scope(self):
        with self.assertRaises(ValueError):Interview('pairs','unknown')
        with self.assertRaises(ValueError):Interview('pairs','owned').step('x'*4097,Agent())
        session=Interview('pairs','owned',rounds=12)
        with self.assertRaises(ValueError):session.step('',Agent())

    def test_runtime_model_and_endpoint_are_constrained(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            def config(model,endpoint):
                path.write_text(json.dumps({'model':'lmstudio/'+model,'provider':{'lmstudio':{'options':{'baseURL':endpoint}}}}))
            config(MODEL,'http://127.0.0.1:1234/v1')
            client=LocalGemma(path)
            self.assertEqual(client.model,MODEL)
            for model,url in [('other-model','http://127.0.0.1:1234/v1'),(MODEL,'https://example.com/v1'),(MODEL,'http://user:secret@127.0.0.1/v1')]:
                config(model,url)
                with self.assertRaises(ValueError):LocalGemma(path)

    def test_explicit_unknown_in_initial_dump_is_not_asked_again(self):
        session=Interview('pairs','owned')
        agent=Agent({'facts':[{'key':'name','value':'John','evidence':'John'}], 'unknown_facts':[{'key':'occupation','evidence':"I don't know his job"}], 'next_topic':'approved_words'})
        self.assertEqual(session.step("John. I don't know his job.",agent),'approved_words')
        self.assertIn('occupation',session.unknown)
        self.assertNotIn('occupation',session.available)

    def test_unknown_is_not_inferred_from_positive_evidence(self):
        session=Interview('pairs','owned')
        with self.assertRaises(ValueError):
            session.step('John',Agent({'facts':[], 'unknown_facts':[{'key':'occupation','evidence':'John'}], 'next_topic':None}))
