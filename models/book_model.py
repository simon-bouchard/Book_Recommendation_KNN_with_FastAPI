import pandas as pd
import numpy as np
from scipy.sparse import csr_matrix
from sklearn.neighbors import NearestNeighbors
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

user_book_matrix = None
sparse_matrix = None
book_model = None
isbn_to_index = None

load_dotenv()
client = AsyncIOMotorClient(os.getenv('MONGO_URI'))
db = client['book-recommendation']
books = db['Books']
ratings = db['ratings']

async def reload_model():
    print('Model reload...')
    global user_book_matrix, sparse_matrix, book_model, isbn_to_index

    ratings_cursor = ratings.find()
    ratings_list = await ratings_cursor.to_list(None)
    df_ratings = pd.DataFrame(ratings_list)

    if df_ratings.empty:
        return

    user_count = df_ratings['user_id'].value_counts()
    valid_users = user_count[user_count >= 200].index

    book_count = df_ratings['isbn'].value_counts()
    valid_books = book_count[book_count >= 100].index

    df_ratings = df_ratings[(df_ratings['user_id'].isin(valid_users)) & (df_ratings['isbn'].isin(valid_books))]

    df_ratings['rating'] = df_ratings['rating'].astype(int)

    user_book_matrix = df_ratings.pivot(index='isbn', columns='user_id', values='rating').fillna(0)
    sparse_matrix = csr_matrix(user_book_matrix.values)

    book_model = NearestNeighbors(metric='cosine', algorithm='brute')
    book_model.fit(sparse_matrix)

    isbn_to_index = {isbn: idx for idx, isbn in enumerate(user_book_matrix.index)}

    print('Model reloaded')

async def get_recommendations(book: str, isbn: bool = True):

    if isbn:
        book_isbn = book
    else:
        book_entry = await books.find_one({'title': book})
        book_isbn = book_entry.get('isbn')

    if not book_isbn:
        return { 'error': "Book not found (books with less than 100 ratings can't get recommendations)"}

    if book_model is None or user_book_matrix is None or isbn_to_index is None:
        await reload_model()
    
    if book_isbn not in isbn_to_index:
        return { 'error': "Book not found (books with less than 100 ratings can't get recommendations)"}

    query_index = isbn_to_index[book_isbn]

    distances, indices = book_model.kneighbors(sparse_matrix[query_index], n_neighbors=6)

    recommendations = []

    for idx, dist in zip(indices[0][-1:0:-1], distances[0][-1:0:-1]):
        neighbor_isbn = user_book_matrix.index[idx]
        book_result = await books.find_one({'isbn': neighbor_isbn})
        if book_result: 
            recommendations.append({'title': book_result['title'], 'similarity': round(float(dist), 2)})
        
    return recommendations

