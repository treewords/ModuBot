from discord.ext.commands import Cog, command
from asyncio import create_task, Lock
import traceback
from ...rich_guild import get_guild
from ...decorator_helper import decorate_cog_command
from ...playback import Entry, Playlist
from ...utils import fixg, ftimedelta
from .ytdldownloader import YtdlDownloader, get_entry
from collections import defaultdict
from ...playback import PlayerState
import time
import re

deps = ['permission']

class YtdlEntry(Entry):
    async def prepare_cache(self):
        async with self._aiolocks['preparing_cache_set']:
            self._preparing_cache = True

        async with self._aiolocks['preparing_cache_set']:
            async with self._aiolocks['cached_set']:
                self._preparing_cache = False
                self._cached = True

class Music(Cog):
    def __init__(self):
        self._aiolocks = defaultdict(Lock)
        self.bot = None
        self.downloader = None
        self._playlists = dict()

    async def pre_init(self, bot, config):
        self.bot = bot
        self.downloader = YtdlDownloader(self.bot, 'audio_cache')

    async def init(self):
        self.bot.crossmodule.assign_dict_object('PermissivePerm', 'canSummon', 'True')
        self.bot.crossmodule.assign_dict_object('PermissivePerm', 'canDisconnect', 'True')
        self.bot.crossmodule.assign_dict_object('PermissivePerm', 'canControlPlayback', 'True')
        self.bot.crossmodule.assign_dict_object('PermissivePerm', 'canAddEntry', 'True')
        self.bot.crossmodule.assign_dict_object('DefaultPerm', 'canSummon', 'False')
        self.bot.crossmodule.assign_dict_object('DefaultPerm', 'canDisconnect', 'False')
        self.bot.crossmodule.assign_dict_object('DefaultPerm', 'canAddEntry', 'False')

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canSummon', 'True')
    async def summon(self, ctx):
        """
        Usage:
            {prefix}summon

        summon bot into voice channel that you're currently joining to
        """
        voicestate = ctx.author.voice
        voicechannel = None
        if voicestate:
            voicechannel = voicestate.channel

        if not voicechannel:
            raise Exception("not in any voice channel")

        else:
            guild = get_guild(ctx.bot, ctx.guild)
            await guild.set_connected_voice_channel(voicechannel)
            playlist = await guild.get_playlist()
            if not playlist:
                playlistname = 'default-{}'.format(guild.id)
                if playlistname not in self._playlists:
                    self._playlists[playlistname] = Playlist(ctx.bot, playlistname)
                await guild.set_playlist(self._playlists[playlistname])
            await ctx.send('successfully summoned')

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canDisconnect', 'True')
    async def disconnect(self, ctx):
        """
        Usage:
            {prefix}disconnect

        disconnect bot from voice channel
        """
        guild = get_guild(ctx.bot, ctx.guild)
        await guild.set_connected_voice_channel(None)
        await ctx.send('successfully disconnected')

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canControlPlayback', 'True')
    async def resume(self, ctx):
        """
        Usage:
            {prefix}resume

        resume playback
        """
        guild = get_guild(ctx.bot, ctx.guild)
        player = await guild.get_player()
        def fail(exc):
            async def _fail():
                exceptionstr = 'Cannot resume! {}'.format(str(exc))
                ctx.bot.log.error(exceptionstr)
                await ctx.send(exceptionstr)
            create_task(_fail())
        def success():
            async def _success():
                await ctx.send('successfully resumed')
            create_task(_success())
        await player.play(play_fail_cb = fail, play_success_cb = success)

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canControlPlayback', 'True')
    async def pause(self, ctx):
        """
        Usage:
            {prefix}pause

        pause playback
        """
        guild = get_guild(ctx.bot, ctx.guild)
        player = await guild.get_player()
        await player.pause()
        await ctx.send('successfully paused')

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canControlPlayback', 'True')
    async def skip(self, ctx):
        """
        Usage:
            {prefix}skip

        skip playback
        """
        guild = get_guild(ctx.bot, ctx.guild)
        player = await guild.get_player()
        await player.skip()
        await ctx.send('successfully skipped')

    @command()
    @decorate_cog_command('require_perm_cog_command', 'canAddEntry', 'True')
    async def play(self, ctx, song_url: str):
        """
        Usage:
            {command_prefix}play song_link
            {command_prefix}play text to search for

        Adds the song to the current playlist.
        """
        guild = get_guild(ctx.bot, ctx.guild)
        player = await guild.get_player()

        song_url = song_url.strip('<>')
            
        # Make sure forward slashes work properly in search queries
        linksRegex = '((http(s)*:[/][/]|www.)([a-z]|[A-Z]|[0-9]|[/.]|[~])*)'
        pattern = re.compile(linksRegex)
        matchUrl = pattern.match(song_url)
        song_url = song_url.replace('/', '%2F') if matchUrl is None else song_url

        # Rewrite YouTube playlist URLs if the wrong URL type is given
        playlistRegex = r'watch\?v=.+&(list=[^&]+)'
        matches = re.search(playlistRegex, song_url)
        groups = matches.groups() if matches is not None else []
        song_url = "https://www.youtube.com/playlist?" + groups[0] if len(groups) > 0 else song_url

        # TODO: check perm
        # This lock prevent spamming play command to add entries that exceeds time limit/ maximum song limit
        async with self._aiolocks['play_{}'.format(ctx.author.id)]:
            # Try to determine entry type, if _type is playlist then there should be entries
            info, song_url = await self.downloader.process_url_to_info(
                song_url,
                on_search_error = lambda e: create_task(
                    ctx.send("```\n%s\n```" % e)
                )
            )

            # TODO: check extractor

            # If it's playlist
            if 'entries' in info:
                # TODO: check permission for playlist

                num_songs = sum(1 for _ in info['entries'])

                if info['extractor'].lower() in ['youtube:playlist', 'soundcloud:set', 'bandcamp:album']:
                    # TODO: play playlist async
                    pass

                t0 = time.time()

                # My test was 1.2 seconds per song, but we maybe should fudge it a bit, unless we can
                # monitor it and edit the message with the estimated time, but that's some ADVANCED SHIT
                # I don't think we can hook into it anyways, so this will have to do.
                # It would probably be a thread to check a few playlists and get the speed from that
                # Different playlists might download at different speeds though
                wait_per_song = 1.2

                procmesg = await ctx.send(
                    'Gathering playlist information for {0} songs{1}'.format(
                        num_songs,
                        ', ETA: {0} seconds'.format(
                            fixg(num_songs * wait_per_song)
                        ) if num_songs >= 10 else '.'
                    )
                )

                async with ctx.typing():

                    # TODO: I can create an event emitter object instead, add event functions, and every play list might be asyncified
                    #       Also have a "verify_entry" hook with the entry as an arg and returns the entry if its ok

                    # TODO: playlist import entry
                    playlist = await player.get_playlist()
                    entry_list, position = await playlist.import_from(song_url, channel=ctx.channel, author=ctx.author)

                    tnow = time.time()
                    ttime = tnow - t0
                    listlen = len(entry_list)
                    drop_count = 0

                    # TODO: check perm for length of each entry

                    ctx.bot.log.info("Processed {} songs in {} seconds at {:.2f}s/song, {:+.2g}/song from expected ({}s)".format(
                        listlen,
                        fixg(ttime),
                        ttime / listlen if listlen else 0,
                        ttime / listlen - wait_per_song if listlen - wait_per_song else 0,
                        fixg(wait_per_song * num_songs))
                    )

                    await procmesg.delete()

                    reply_text = "Enqueued **%s** songs to be played. Position in queue: %s"
                    btext = str(listlen - drop_count)

            # If it's an entry
            else:
                # youtube:playlist extractor but it's actually an entry
                if info.get('extractor', '').startswith('youtube:playlist'):
                    try:
                        info = await self.downloader.extract_info(ctx.bot.loop, 'https://www.youtube.com/watch?v=%s' % info.get('url', ''), download=False, process=False)
                    except Exception as e:
                        raise e

                # TODO: check permission for entry

                playlist = await player.get_playlist()
                entry = await get_entry(song_url, self.downloader, {'channel':ctx.channel, 'author':ctx.author})
                position = await playlist.add_entry(entry)

                reply_text = "Enqueued `%s` to be played. Position in queue: %s"
                btext = entry.title

            # Position msgs

        await ctx.send(reply_text)

cogs = [Music]