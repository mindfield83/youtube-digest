#!/usr/bin/env python3
"""
Live E2E Test Script for YouTube Digest

Tests the full pipeline:
1. YouTube API - Fetch subscriptions
2. YouTube API - Fetch videos from channels
3. Transcript Service - Get transcript
4. Summarization Service - Generate summary with Gemini
5. Celery Tasks - Trigger check_for_new_videos
"""
import sys
from datetime import datetime, timedelta

def test_youtube_api():
    """Test YouTube API connectivity."""
    print("\n=== TEST 1: YouTube API ===")
    from app.services.youtube_service import YouTubeService

    service = YouTubeService()
    subs = service.get_subscriptions()
    print(f"✓ Found {len(subs)} subscriptions")

    if subs:
        print(f"  Sample channels:")
        for sub in subs[:3]:
            print(f"    - {sub['channel_name']}")

    return subs

def test_channel_videos(subs):
    """Test fetching videos from a channel."""
    print("\n=== TEST 2: Channel Videos ===")
    from app.services.youtube_service import YouTubeService

    if not subs:
        print("✗ No subscriptions to test")
        return None

    service = YouTubeService()
    since = datetime.utcnow() - timedelta(days=14)

    all_videos = []
    for sub in subs[:5]:
        videos = service.get_channel_videos(sub['channel_id'], since)
        if videos:
            all_videos.extend(videos)
            print(f"✓ {sub['channel_name']}: {len(videos)} videos")

    print(f"✓ Total videos found: {len(all_videos)}")
    return all_videos[0] if all_videos else None

def test_transcript(video):
    """Test transcript fetching."""
    print("\n=== TEST 3: Transcript Service ===")
    from app.services.transcript_service import TranscriptService

    if not video:
        print("✗ No video to test")
        return None

    service = TranscriptService()
    video_id = video['video_id']
    title = video['title']

    print(f"  Testing: {title}")

    try:
        result = service.get_transcript(video_id)
        print(f"✓ Transcript fetched")
        print(f"  Source: {result.source}")
        print(f"  Language: {result.language}")
        print(f"  Length: {len(result.text)} chars")
        return result.text
    except Exception as e:
        print(f"✗ Transcript error: {e}")
        return None

def test_summarization(transcript, video):
    """Test Gemini summarization."""
    print("\n=== TEST 4: Summarization Service ===")
    from app.services.summarization_service import SummarizationService
    from app.config import settings

    if not transcript:
        print("✗ No transcript to summarize")
        return None

    if not settings.gemini_api_key:
        print("✗ No Gemini API key configured")
        return None

    service = SummarizationService()

    try:
        summary = service.summarize_video(
            transcript=transcript,
            title=video['title'],
            channel_name=video.get('channel_name', 'Unknown'),
            duration_seconds=video.get('duration', 0)
        )
        print(f"✓ Summary generated")
        print(f"  Category: {summary.category.value}")
        print(f"  Core message: {summary.core_message[:100]}...")
        print(f"  Key takeaways: {len(summary.key_takeaways)}")
        return summary
    except Exception as e:
        print(f"✗ Summarization error: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_celery_task():
    """Test Celery task execution."""
    print("\n=== TEST 5: Celery Tasks ===")
    from app.tasks import check_for_new_videos

    try:
        result = check_for_new_videos.delay()
        print(f"✓ Task queued: {result.id}")
        print("  (Task runs asynchronously)")
        return True
    except Exception as e:
        print(f"✗ Celery error: {e}")
        return False

def main():
    print("=" * 60)
    print("YouTube Digest - Live E2E Test")
    print("=" * 60)
    print(f"Started: {datetime.now().isoformat()}")

    results = {}

    # Test 1: YouTube API
    try:
        subs = test_youtube_api()
        results['youtube_api'] = len(subs) > 0
    except Exception as e:
        print(f"✗ YouTube API failed: {e}")
        results['youtube_api'] = False
        subs = []

    # Test 2: Channel Videos
    try:
        video = test_channel_videos(subs)
        results['channel_videos'] = video is not None
    except Exception as e:
        print(f"✗ Channel videos failed: {e}")
        results['channel_videos'] = False
        video = None

    # Test 3: Transcript
    try:
        transcript = test_transcript(video)
        results['transcript'] = transcript is not None
    except Exception as e:
        print(f"✗ Transcript failed: {e}")
        results['transcript'] = False
        transcript = None

    # Test 4: Summarization
    try:
        summary = test_summarization(transcript, video)
        results['summarization'] = summary is not None
    except Exception as e:
        print(f"✗ Summarization failed: {e}")
        results['summarization'] = False

    # Test 5: Celery
    try:
        results['celery'] = test_celery_task()
    except Exception as e:
        print(f"✗ Celery failed: {e}")
        results['celery'] = False

    # Summary
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    all_passed = True
    for test, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
