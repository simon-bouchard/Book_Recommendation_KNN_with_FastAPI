import pandas as pd
from fastapi import APIRouter, HTTPException, Form, Request, FastAPI, Depends, status, Body, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import jwt
from datetime import datetime, timedelta
from app.auth import get_current_user
from bson import ObjectId
from models.book_model import reload_model, get_recommendations
from models.user_model import reload_user_model, get_user_recommendations
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="templates")

load_dotenv()
client = MongoClient(os.getenv('MONGO_URI'))
db = client['book-recommendation']
ratings = db['ratings']
books = db['Books']
users = db['users']

@router.get('/login', response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse('login.html', {'request': request})

@router.get('/profile')
def profile_page(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse(url='/login', status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse('profile.html', {'request': request, 'user': current_user})

@router.post('/rating')
async def new_rating(current_user = Depends(get_current_user), data: dict = Body(...), background_tasks = Depends(BackgroundTasks)):

    if not current_user:
        return RedirectResponse(url='/login', status_code=status.HTTP_303_SEE_OTHER)

    isbn = data.get('isbn')
    rating = data.get('rating')
    comment = data.get('comment')

    if not isbn or not rating:
        raise HTTPException(status_code=400, detail='Missing required fields')

    ratings.update_one(
        {'user_id': current_user['_id'], 'isbn': isbn}, 
        {"$set": {'rating': rating, 'comment': comment, 'created_at': datetime.utcnow()}},
        upsert=True
    )

    background_tasks.add_task(reload_model)

    return {'message': f'Rating successfully submitted/updated!'}

@router.get('/book/{isbn}', response_class=HTMLResponse)
async def book_recommendation(request: Request, isbn: str, current_user: dict = Depends(get_current_user)): 
#    book = books_data.get(isbn)

    book = books.find_one({'isbn': isbn})

    pipeline = [
        {"$match": {"isbn": isbn}},
        {"$group": {"_id": "$isbn", "avg_rating": {"$avg": "$rating"}}}
    ]

    average = list(ratings.aggregate(pipeline))

    if average: 
        average = round(average[0]['avg_rating'], 2)

    if not book: 
        raise HTTPException(status_code=404, detail='Book not found')

    book = {
            'isbn': isbn,
            'title': book['title'],
            'author': book['author'],
            'year': book['year'],
            'publisher': book['publisher'],
            'average_rating': average
    }

    user_rating = None

    if current_user: 
        user_rating = ratings.find_one({'user_id': current_user['_id'], 'isbn': isbn})

    return templates.TemplateResponse('book.html', {"request": request, "book": book, 'user_rating': user_rating})

@router.get('/comments')
async def get_comments(book: str = Query(...), isbn: bool = True, limit: int = 5):
    if not isbn:
        db_book = await books.find_one({'title': book})
        if db_book:
            book = db_book['isbn']
        else:
            return {'error': 'Book not found'}
        
    comments = list(ratings.find({'isbn': book, 'comment': {'$ne': '', '$exists': True}}).limit(limit))

    for comment in comments:
        comment['username'] = users.find_one({'_id': comment['user_id']})['username']
        comment['_id'] = str(comment['_id'])
        comment['user_id'] = str(comment['user_id'])

    if not comments:
        raise HTTPException(status_code=404, detail='No comments have been submitted for this book yet')
    return comments

@router.get('/recommend')
async def recommend_books(book: str = Query(...), isbn: bool = True):
    recommendations = await get_recommendations(book, isbn)

    if 'error' in recommendations:
        raise HTTPException(status_code=404, detail=recommendations['error'])
    if recommendations:
        return recommendations
    else:
        raise HTTPException(status_code=404, detail="Book not found (books with less than 100 ratings can't have recommendations.")

@router.get('/profile/recommend')
async def user_recommendation(user: str, _id: bool = True, n: int = 5):
    if _id:
        if not ObjectId.is_valid(user):
            raise HTTPException(status_code=404, detail='Invalid BSON object format')
        user = ObjectId(user)
    recommendations = await get_user_recommendations(user, _id, n)

    if 'error' in recommendations:
        raise HTTPException(status_code=404, detail=recommendations['error'])
    if recommendations:
        return recommendations
    else:
        raise HTTPException(status_code=404, detail='Unexpected error retrieving recommendations')

@router.get('/logout')
async def logout():
    response = RedirectResponse(url='/login', status_code=status.HTTP_303_SEE_OTHER)

    response.delete_cookie(key='access_token')

    return response

