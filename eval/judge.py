"""LongMemEval's own judge prompts, copied verbatim (ADR 0007, 0008).

Source: https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py
(`get_anscheck_prompt`). The official run uses gpt-4o-2024-08-06 at temperature 0, max_tokens 10,
and counts the answer as correct when 'yes' is in the reply. We keep the prompts and the rule;
the judge *model* is configurable and recorded in every results file.
"""


def anscheck_prompt(task: str, question: str, answer: str, response: str, abstention: bool) -> str:
    if abstention:
        template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
    elif task in ("single-session-user", "single-session-assistant", "multi-session"):
        template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
    elif task == "temporal-reasoning":
        template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
    elif task == "knowledge-update":
        template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
    elif task == "single-session-preference":
        template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
    else:
        raise NotImplementedError(task)
    return template.format(question, answer, response)


def locomo_prompt(question: str, answer: str, response: str) -> str:
    """Our own wording, NOT an official LoCoMo grader (its metric is token F1). ADR 0017.

    Same reply rule as LongMemEval: correct when 'yes' is in the reply.
    """
    return (
        "I will give you a question, a correct answer, and a response from a model. Please answer "
        "yes if the response contains the correct answer. Otherwise, answer no. If the response "
        "is equivalent to the correct answer, or gives the same fact in different words, answer "
        "yes. If it only contains a subset of the information required, answer no. If the correct "
        "answer is a date, accept any format that names the same date, month or year as the "
        "correct answer asks for.\n\n"
        f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
        "Is the model response correct? Answer yes or no only."
    )


def is_correct(judge_reply: str) -> bool:
    return "yes" in judge_reply.strip().lower()  # the official rule
