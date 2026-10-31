import argparse
import json
import os
import sqlite3
import sys
from .providers import OpenAIBackend
from .retrieval import RAG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='data/index.sqlite')
    sub = parser.add_subparsers(dest='command', required=True)
    ingest = sub.add_parser('ingest')
    ingest.add_argument('--docs', default='docs')
    for command in ('ask', 'search', 'chat'):
        p = sub.add_parser(command)
        if command != 'chat':
            p.add_argument('question')
        p.add_argument('--top-k', type=int, default=4)
        p.add_argument('--min-score', type=float, default=-1.0)
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
        rag = RAG(args.db, OpenAIBackend(), os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small'))
        if args.command == 'ingest':
            print(json.dumps(rag.ingest(args.docs), indent=2))
        elif args.command == 'chat':
            print('Governance Q&A. Each question is independent. Type /quit to exit.')
            while True:
                try:
                    question = input('\nYou: ').strip()
                except EOFError:
                    break
                if question == '/quit':
                    break
                if question:
                    print(json.dumps(rag.ask(question, args.top_k, args.min_score), indent=2))
        else:
            result = (rag.retrieve if args.command == 'search' else rag.ask)(args.question, args.top_k, args.min_score)
            print(json.dumps(result, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0
