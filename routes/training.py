"""API routes for the Video Training Library."""

import json
from flask import Blueprint, jsonify, request

from utils.database import (
    get_all_training_sections, create_training_section, update_training_section,
    delete_training_section, create_training_video, update_training_video,
    delete_training_video, toggle_watch, get_watched_videos, toggle_favorite,
    get_favorite_videos, save_training_note, get_training_notes,
    save_quiz_result, get_best_quiz_results, get_training_leaderboard,
    get_manager_training_dashboard, get_all_people, get_connection,
)

training_bp = Blueprint("training", __name__)


# --- Seed default training content ---

def seed_training_library():
    """Populate the training library with default sections and videos if empty."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) as c FROM training_sections").fetchone()["c"]
    conn.close()
    if count > 0:
        return  # Already seeded

    sections = [
        {
            "title": "Motivation and Mindset",
            "sort_order": 1,
            "videos": [
                {"title": "How to Sell 50 Cars a Month", "description": "Andy Elliott trains a full dealership on the mindset and daily habits needed to sell 50+ cars per month. High-energy session covering work ethic, attitude, and daily discipline.", "duration": "45 min", "difficulty": "beginner", "url": ""},
                {"title": "The Mindset of a Top 1% Car Salesman", "description": "What separates average performers from elite salespeople. Covers mental toughness, handling rejection, and maintaining a winning attitude through slow months.", "duration": "18 min", "difficulty": "beginner", "url": ""},
                {"title": "Why Most Salespeople Fail (And How to Fix It)", "description": "Common mindset traps that keep salespeople stuck at 8-10 cars/month. Includes actionable steps to break through mental barriers and build confidence.", "duration": "22 min", "difficulty": "beginner", "url": ""},
                {"title": "Morning Routine of Top Car Sales Performers", "description": "How the best performers start their day before they even get to the dealership. Covers visualization, goal review, and energy management.", "duration": "15 min", "difficulty": "beginner", "url": ""},
                {"title": "Turning a Bad Month Around", "description": "Strategies for recovering when you're behind on your numbers. Mental resets, pipeline management, and maximizing remaining selling days.", "duration": "20 min", "difficulty": "intermediate", "url": ""},
                {"title": "Building Unshakable Confidence on the Lot", "description": "Techniques for projecting confidence even when you're new or going through a slump. Body language, self-talk, and preparation strategies.", "duration": "16 min", "difficulty": "beginner", "url": ""},
                {"title": "The Power of Persistence in Automotive Sales", "description": "Real stories and data showing why consistent follow-through beats natural talent. Includes the 'Rule of 7' touches and why most salespeople give up too early.", "duration": "14 min", "difficulty": "beginner", "url": ""},
                {"title": "From 10 Cars to 30 Cars a Month: A Transformation Story", "description": "Case study of a salesperson who tripled their production. Breaks down the specific changes in mindset, process, and daily habits that made the difference.", "duration": "25 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Goal Setting",
            "sort_order": 2,
            "videos": [
                {"title": "Reverse-Engineering Your Income Goal", "description": "Start with your desired annual income and work backwards to daily activities. Covers closing ratios, average gross, and the exact number of ups, calls, and follow-ups needed.", "duration": "20 min", "difficulty": "beginner", "url": ""},
                {"title": "Monthly and Weekly Planning for Car Sales", "description": "How to break down monthly targets into weekly and daily action plans. Includes templates for tracking prospecting activities, appointments, and deliveries.", "duration": "18 min", "difficulty": "beginner", "url": ""},
                {"title": "CRM Goal Tracking Walkthroughs", "description": "Step-by-step guide to setting up your CRM to track goals, pipeline stages, and daily activities. Making your CRM work for you instead of just logging data.", "duration": "25 min", "difficulty": "intermediate", "url": ""},
                {"title": "Building a Business Plan as a Car Salesperson", "description": "Treat your sales career like a business. Creating a 90-day plan with specific targets for prospecting, repeat/referral business, and income milestones.", "duration": "22 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Meet and Greet",
            "sort_order": 3,
            "videos": [
                {"title": "The Perfect Meet and Greet: First 30 Seconds", "description": "How to approach a customer on the lot without being pushy. Covers body language, smile, handshake, and the opening line that builds immediate rapport.", "duration": "12 min", "difficulty": "beginner", "url": ""},
                {"title": "Building Rapport in the First 2 Minutes", "description": "Techniques for quickly connecting with customers. Finding common ground, reading body language, and making the customer feel comfortable and valued.", "duration": "15 min", "difficulty": "beginner", "url": ""},
                {"title": "Handling the 'Just Looking' Customer", "description": "The most common brush-off and how to gracefully move past it. Word tracks and approaches that turn 'just looking' into a real conversation.", "duration": "10 min", "difficulty": "beginner", "url": "",
                 "quiz": [{"question": "What should you do first when a customer says 'I'm just looking'?", "options": ["Walk away and give them space", "Acknowledge it and take the pressure off", "Start listing vehicle features immediately", "Ask them their budget"], "answer": 1}, {"question": "What's the main goal of the meet and greet?", "options": ["Qualify the customer's budget", "Build trust and rapport", "Get them to commit to a test drive", "Show them the newest inventory"], "answer": 1}]},
                {"title": "Internet Lead Meet and Greet vs. Walk-In", "description": "How the greeting differs for internet appointments vs. walk-in traffic. Setting expectations, confirming interests, and transitioning smoothly to needs assessment.", "duration": "14 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Qualifying / Choosing the Right Vehicle",
            "sort_order": 4,
            "videos": [
                {"title": "The Needs Assessment: Discovery Questions That Work", "description": "The essential questions to uncover what a customer truly needs. Open-ended vs. closed questions, and how to dig deeper without feeling like an interrogation.", "duration": "18 min", "difficulty": "beginner", "url": ""},
                {"title": "Matching Customer Needs to Inventory", "description": "How to listen to what a customer says and translate it into the right vehicle selection. Techniques for steering toward in-stock units that fit their needs.", "duration": "15 min", "difficulty": "beginner", "url": ""},
                {"title": "Trade-In Evaluation Basics for Salespeople", "description": "What salespeople need to know about evaluating trade-ins. Walking the trade, asking the right questions, and setting realistic expectations without losing the deal.", "duration": "20 min", "difficulty": "intermediate", "url": ""},
                {"title": "Feature/Benefit Presentation Techniques", "description": "How to translate vehicle features into benefits that matter to each specific customer. Connecting trim levels and packages to their stated needs and lifestyle.", "duration": "16 min", "difficulty": "beginner", "url": "",
                 "quiz": [{"question": "What's the difference between a feature and a benefit?", "options": ["Features are expensive, benefits are free", "A feature is what it has, a benefit is what it does for the customer", "Features are for new cars, benefits are for used cars", "There is no difference"], "answer": 1}, {"question": "When should you present vehicle features?", "options": ["Before the needs assessment", "After understanding the customer's needs", "Only during negotiation", "Only if the customer asks"], "answer": 1}]},
                {"title": "Switching a Customer to a Different Vehicle", "description": "How to gracefully redirect a customer from a vehicle that's wrong for them (or not in stock) to one that's a better fit. Reframing without losing trust.", "duration": "14 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Demo / Walk-Around",
            "sort_order": 5,
            "videos": [
                {"title": "The 6-Point Walk-Around Method", "description": "A systematic approach to presenting any vehicle. Covers the six key positions around the car and what to highlight at each stop, tied to customer hot buttons.", "duration": "20 min", "difficulty": "beginner", "url": ""},
                {"title": "Making Features Come Alive During the Demo", "description": "How to demonstrate features (not just point them out). Hands-on techniques for showing technology, safety features, and comfort that create emotional connection.", "duration": "18 min", "difficulty": "intermediate", "url": ""},
                {"title": "40 Yesses: Building Agreement During the Walk-Around", "description": "The classic technique of asking micro-commitment questions throughout the presentation. Getting the customer saying 'yes' repeatedly builds momentum toward the close.", "duration": "15 min", "difficulty": "intermediate", "url": ""},
                {"title": "Walk-Around for Used Vehicles", "description": "How the presentation differs for pre-owned inventory. Addressing condition transparency, highlighting value, and building confidence in a used vehicle purchase.", "duration": "16 min", "difficulty": "beginner", "url": ""},
            ],
        },
        {
            "title": "Test Drive",
            "sort_order": 6,
            "videos": [
                {"title": "Running an Effective Test Drive", "description": "How to structure the test drive for maximum impact. Choosing the right route, what to say during the drive, and how to let the car sell itself.", "duration": "14 min", "difficulty": "beginner", "url": ""},
                {"title": "Test Drive Safety and Legal Basics", "description": "Protecting yourself and the dealership during test drives. License checks, insurance verification, and safe driving route selection.", "duration": "10 min", "difficulty": "beginner", "url": ""},
                {"title": "The Silent Test Drive Technique", "description": "When to stop talking and let the customer experience the vehicle. Reading cues for when to highlight features vs. when silence sells better.", "duration": "12 min", "difficulty": "intermediate", "url": ""},
                {"title": "Closing During and After the Test Drive", "description": "Trial close techniques to use during the drive and the critical transition from test drive back to the desk. 'If the numbers work, are you ready to take it home today?'", "duration": "16 min", "difficulty": "intermediate", "url": "",
                 "quiz": [{"question": "What is a trial close?", "options": ["A final price offer", "A question that tests the customer's buying temperature", "A test drive with no commitment", "A manager's closing technique"], "answer": 1}, {"question": "When is the best time for a trial close during a test drive?", "options": ["Before they start the car", "When they're enjoying a feature you highlighted", "After they've parked back at the dealership", "Never during the test drive"], "answer": 1}]},
            ],
        },
        {
            "title": "Negotiation / Desking",
            "sort_order": 7,
            "videos": [
                {"title": "First Pencil Presentation Basics", "description": "How to present the first set of numbers with confidence. Structuring the initial offer, anchoring high, and managing customer expectations from the start.", "duration": "22 min", "difficulty": "intermediate", "url": ""},
                {"title": "Overcoming Price Objections", "description": "Word tracks and strategies for when customers say 'the price is too high.' Reframing value, monthly payment discussions, and knowing when to involve the manager.", "duration": "20 min", "difficulty": "intermediate", "url": ""},
                {"title": "Payment vs. Price: Guiding the Conversation", "description": "How to steer negotiations toward monthly payment rather than total price when it benefits the deal. Understanding what the customer is really negotiating.", "duration": "18 min", "difficulty": "advanced", "url": ""},
                {"title": "Working with the Desk Manager", "description": "How to be an effective liaison between the customer and the sales manager. Presenting deals, getting bumps, and maintaining customer trust throughout the process.", "duration": "16 min", "difficulty": "intermediate", "url": ""},
                {"title": "Four Square Method Explained", "description": "Understanding the four-square negotiation worksheet. How it works, when to use it, and how to present numbers in a way that keeps all four elements flexible.", "duration": "25 min", "difficulty": "advanced", "url": ""},
            ],
        },
        {
            "title": "F&I Handoff",
            "sort_order": 8,
            "videos": [
                {"title": "Smooth Transition to the Finance Office", "description": "How to hand off a customer to F&I without losing the excitement of the deal. Setting expectations, introducing the finance manager, and maintaining momentum.", "duration": "12 min", "difficulty": "beginner", "url": ""},
                {"title": "Menu Selling Basics for Salespeople", "description": "Understanding the F&I products your dealership offers so you can set the stage during the sales process. Warranties, GAP, maintenance plans — what they are and why they matter.", "duration": "18 min", "difficulty": "intermediate", "url": ""},
                {"title": "What Salespeople Should Know About F&I", "description": "The sales-F&I partnership and how it affects your paycheck. Understanding backend gross, reserve, and how a smooth handoff impacts overall dealer profitability.", "duration": "15 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Follow-Up / Be-Backs",
            "sort_order": 9,
            "videos": [
                {"title": "Follow-Up Cadence: The First 72 Hours", "description": "The critical follow-up timeline after a customer leaves without buying. Exact timing, messaging, and channels (phone, text, email) for maximum be-back rate.", "duration": "16 min", "difficulty": "beginner", "url": ""},
                {"title": "Text and Email Templates That Get Responses", "description": "Proven follow-up message templates for different scenarios: left without buying, waiting on trade value, thinking it over, or shopping competitors.", "duration": "14 min", "difficulty": "beginner", "url": ""},
                {"title": "Turning Be-Backs Into Buyers", "description": "When a customer comes back, the approach is different than a fresh up. How to pick up where you left off, address what's changed, and close on the return visit.", "duration": "18 min", "difficulty": "intermediate", "url": ""},
                {"title": "Building a Repeat and Referral Business", "description": "Long-term follow-up strategies for sold customers. Anniversary calls, service reminders, and referral requests that build a self-sustaining book of business.", "duration": "20 min", "difficulty": "advanced", "url": ""},
            ],
        },
        {
            "title": "Phone Skills / BDC",
            "sort_order": 10,
            "videos": [
                {"title": "Inbound Call Handling for Sales", "description": "How to answer a sales call professionally, build rapport quickly, and convert the call into a showroom appointment. Includes scripts for common inbound scenarios.", "duration": "18 min", "difficulty": "beginner", "url": ""},
                {"title": "Outbound Prospecting Calls That Work", "description": "Cold calling and warm calling techniques for generating your own business. Scripts, objection handling, and how to make 20+ productive calls per day.", "duration": "22 min", "difficulty": "intermediate", "url": ""},
                {"title": "Setting Appointments That Show", "description": "The appointment is only valuable if the customer actually comes in. Confirmation techniques, value statements, and reducing no-show rates.", "duration": "15 min", "difficulty": "beginner", "url": "",
                 "quiz": [{"question": "When should you confirm an appointment?", "options": ["Only when the appointment is set", "The day before and the morning of", "Only if the customer asks", "One week before"], "answer": 1}, {"question": "What's the best way to reduce no-shows?", "options": ["Call to remind them about the sale price", "Build enough value that they feel they'd miss out", "Threaten that the deal expires", "Send multiple emails"], "answer": 1}]},
                {"title": "BDC vs. Floor Sales: Understanding the Handoff", "description": "How BDC and floor sales teams work together. Setting proper expectations during the call so the floor salesperson can deliver on the appointment.", "duration": "14 min", "difficulty": "intermediate", "url": ""},
            ],
        },
        {
            "title": "Objection Handling",
            "sort_order": 11,
            "videos": [
                {"title": "Step-by-Step Objection Handling Framework", "description": "Andy Elliott's systematic approach to handling any objection. The 4-step framework: Listen, Acknowledge, Respond, Advance. Live role-play demonstrations.", "duration": "30 min", "difficulty": "beginner", "url": "https://www.youtube.com/watch?v=Zf0_WA0UanU"},
                {"title": "'I Need to Think About It' — How to Respond", "description": "The most common stall objection and proven word tracks to move past it. Understanding what the customer really means and addressing the real concern underneath.", "duration": "14 min", "difficulty": "beginner", "url": ""},
                {"title": "'The Payment Is Too High' — Overcoming Price Resistance", "description": "Specific techniques for when customers push back on monthly payments. Restructuring the deal, adjusting terms, and finding the real number that works.", "duration": "16 min", "difficulty": "intermediate", "url": ""},
                {"title": "'I Need to Talk to My Spouse' — Handling Third-Party Objections", "description": "How to handle it when the decision-maker isn't present. Getting the absent party involved, phone closes, and creating urgency without pressure.", "duration": "15 min", "difficulty": "intermediate", "url": ""},
                {"title": "'Your Price Is Higher Than Online' — Internet Price Objections", "description": "Addressing competitive pricing in the age of TrueCar, CarGurus, and online comparisons. Building value beyond price and handling the informed buyer.", "duration": "18 min", "difficulty": "advanced", "url": ""},
            ],
        },
        {
            "title": "Closing Techniques",
            "sort_order": 12,
            "videos": [
                {"title": "The Assumptive Close", "description": "How to guide the conversation as if the customer has already decided to buy. Language patterns, paperwork transitions, and reading buying signals.", "duration": "14 min", "difficulty": "beginner", "url": ""},
                {"title": "Trial Closes Throughout the Sales Process", "description": "You don't close once at the end — you close throughout. Embedding mini-closes from the meet and greet through the test drive and desk.", "duration": "18 min", "difficulty": "intermediate", "url": ""},
                {"title": "The Urgency Close: Creating FOMO Ethically", "description": "Legitimate ways to create urgency without lying. Inventory scarcity, incentive deadlines, rate changes, and helping the customer understand the cost of waiting.", "duration": "16 min", "difficulty": "intermediate", "url": ""},
                {"title": "Closing on the First Visit", "description": "Why same-day delivery is the goal and how to structure the entire visit toward that outcome. Overcoming the 'I want to sleep on it' mindset.", "duration": "20 min", "difficulty": "advanced", "url": "",
                 "quiz": [{"question": "What's the main reason customers say they want to 'sleep on it'?", "options": ["They're genuinely tired", "They haven't been given enough reason to buy today", "They always need more time", "The price is always too high"], "answer": 1}, {"question": "When should the closing process begin?", "options": ["After the test drive", "During the negotiation", "From the very first interaction", "Only when the customer says they're ready"], "answer": 2}, {"question": "What is an assumptive close?", "options": ["Assuming the customer can't afford it", "Acting as if the customer has already decided to buy", "Assuming the deal is done without paperwork", "Closing without the customer present"], "answer": 1}]},
            ],
        },
    ]

    for sec_data in sections:
        sid = create_training_section(sec_data["title"], sec_data["sort_order"])
        for i, vid in enumerate(sec_data["videos"]):
            quiz = vid.pop("quiz", [])
            create_training_video(
                section_id=sid,
                title=vid["title"],
                url=vid.get("url", ""),
                description=vid.get("description", ""),
                duration=vid.get("duration", ""),
                difficulty=vid.get("difficulty", "beginner"),
                sort_order=i,
                quiz_json=json.dumps(quiz),
            )


# --- Sections & Videos ---

@training_bp.route("/api/training/sections", methods=["GET"])
def api_get_sections():
    seed_training_library()
    return jsonify(get_all_training_sections())


@training_bp.route("/api/training/sections", methods=["POST"])
def api_create_section():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    sid = create_training_section(title, data.get("sort_order", 0))
    return jsonify({"id": sid})


@training_bp.route("/api/training/sections/<int:section_id>", methods=["PUT"])
def api_update_section(section_id):
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    ok = update_training_section(section_id, title)
    return jsonify({"updated": ok})


@training_bp.route("/api/training/sections/<int:section_id>", methods=["DELETE"])
def api_delete_section(section_id):
    ok = delete_training_section(section_id)
    return jsonify({"deleted": ok})


@training_bp.route("/api/training/videos", methods=["POST"])
def api_create_video():
    data = request.get_json(force=True)
    required = ["section_id", "title"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} required"}), 400
    vid = create_training_video(
        section_id=data["section_id"],
        title=data["title"],
        url=data.get("url", ""),
        description=data.get("description", ""),
        duration=data.get("duration", ""),
        difficulty=data.get("difficulty", "beginner"),
        sort_order=data.get("sort_order", 0),
        quiz_json=json.dumps(data.get("quiz", [])),
    )
    return jsonify({"id": vid})


@training_bp.route("/api/training/videos/<int:video_id>", methods=["PUT"])
def api_update_video(video_id):
    data = request.get_json(force=True)
    kwargs = {}
    for key in ["title", "url", "description", "duration", "difficulty",
                 "sort_order", "section_id"]:
        if key in data:
            kwargs[key] = data[key]
    if "quiz" in data:
        kwargs["quiz_json"] = json.dumps(data["quiz"])
    ok = update_training_video(video_id, **kwargs)
    return jsonify({"updated": ok})


@training_bp.route("/api/training/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    ok = delete_training_video(video_id)
    return jsonify({"deleted": ok})


# --- User-specific: watch, favorites, notes, quizzes ---

@training_bp.route("/api/training/user/<int:person_id>/status", methods=["GET"])
def api_user_status(person_id):
    """Get all user-specific training data in one call."""
    return jsonify({
        "watched": get_watched_videos(person_id),
        "favorites": get_favorite_videos(person_id),
        "notes": get_training_notes(person_id),
        "quiz_results": get_best_quiz_results(person_id),
    })


@training_bp.route("/api/training/user/<int:person_id>/watch/<int:video_id>", methods=["POST"])
def api_toggle_watch(person_id, video_id):
    watched = toggle_watch(person_id, video_id)
    return jsonify({"watched": watched})


@training_bp.route("/api/training/user/<int:person_id>/favorite/<int:video_id>", methods=["POST"])
def api_toggle_favorite(person_id, video_id):
    fav = toggle_favorite(person_id, video_id)
    return jsonify({"favorited": fav})


@training_bp.route("/api/training/user/<int:person_id>/note/<int:video_id>", methods=["POST"])
def api_save_note(person_id, video_id):
    data = request.get_json(force=True)
    save_training_note(person_id, video_id, data.get("note", ""))
    return jsonify({"saved": True})


@training_bp.route("/api/training/user/<int:person_id>/quiz/<int:video_id>", methods=["POST"])
def api_save_quiz(person_id, video_id):
    data = request.get_json(force=True)
    save_quiz_result(person_id, video_id, data["score"], data["total"])
    return jsonify({"saved": True})


# --- Leaderboard & Manager Dashboard ---

@training_bp.route("/api/training/leaderboard", methods=["GET"])
def api_leaderboard():
    return jsonify(get_training_leaderboard())


@training_bp.route("/api/training/manager-dashboard", methods=["GET"])
def api_manager_dashboard():
    return jsonify(get_manager_training_dashboard())


@training_bp.route("/api/training/people", methods=["GET"])
def api_training_people():
    """Get people list for user selector."""
    return jsonify(get_all_people())
