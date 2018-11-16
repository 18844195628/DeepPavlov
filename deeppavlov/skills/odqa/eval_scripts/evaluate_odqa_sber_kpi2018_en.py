"""
Evaluate ODQA on "Sber KPI 2018" for English.
"""

import argparse
import csv

from deeppavlov.core.commands.infer import build_model_from_config
from deeppavlov.core.common.file import read_json

parser = argparse.ArgumentParser()

parser.add_argument('-config_path', help='Path to a JSON config', type=str,
                    default='../../../deeppavlov/configs/odqa/en_odqa_infer_prod.json')
parser.add_argument('-eval_path', help='Path to csv evaluation file', type=str,
                    default='/media/olga/Data/projects/ODQA/data/sber/Sber KPI 2018  - Sheet1.csv')
parser.add_argument('-output_path', help='Path to csv evaluation result', type=str,
                    default='../../../deeppavlov/skills/odqa/eval_logs/sber_kpi2018_en.csv')


def evaluate():
    args = parser.parse_args()

    config = read_json(args.config_path)
    odqa = build_model_from_config(config)

    questions = []
    answers_true = []

    with open(args.eval_path, newline='') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)  # skip header
        for row in reader:
            questions.append(row[0])
            answers_true.append(row[1])

    # skip noise
    questions = questions[:-4]
    answers_true = answers_true[:-4]

    predictions = odqa(questions)

    with open(args.output_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Question', 'True Answer', 'Predicted Answer'])  # add header
        for triple in zip(questions, answers_true, predictions):
            writer.writerow([triple[0], triple[1], triple[2][0]])

    print('Evaluation done.')


if __name__ == "__main__":
    evaluate()

