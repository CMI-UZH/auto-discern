import unittest
import autodiscern.transformations as adt


class TestTransformations(unittest.TestCase):

    def test_remove_html_removes_tags(self):
        test_input = "<h1>I am a Header</h1>"
        expected_output = "I am a Header"
        self.assertEqual(adt.remove_html(test_input), expected_output)

    def test_remove_selected_html_removes_some_keeps_others(self):
        test_input = "<div><h1>I am a Header</h1></div>"
        expected_output = "<h1>I am a Header</h1>"
        self.assertEqual(adt.remove_selected_html(test_input), expected_output)

    def test_replace_problem_chars(self):
        test_input = "words \twords\twords"
        expected_output = "words  words words"
        self.assertEqual(adt.replace_chars(test_input, ['\t'], ' '), expected_output)

    def test_regex_out_periods_and_white_space_replaces_extra_consecutive_chars(self):
        test_input = "text text..\n. text"
        expected_output = "text text. \ntext"
        self.assertEqual(adt.regex_out_periods_and_white_space(test_input), expected_output)

    def test_regex_out_periods_and_white_space_no_effect_single_period(self):
        test_input = "text."
        self.assertEqual(adt.regex_out_periods_and_white_space(test_input), test_input)

    def test_regex_out_periods_and_white_space_removes_double_space_between_words(self):
        test_input = "text  text."
        expected_output = "text text."
        self.assertEqual(adt.regex_out_periods_and_white_space(test_input), expected_output)

    def test_regex_out_periods_and_white_space_no_effect_period_between_words(self):
        test_input = "text. text"
        expected_output = "text. text"
        self.assertEqual(adt.regex_out_periods_and_white_space(test_input), expected_output)

    def test_regex_out_periods_and_white_space_removes_extra_consecutive_periods(self):
        test_input = "text text..."
        expected_output = "text text. "
        self.assertEqual(adt.regex_out_periods_and_white_space(test_input), expected_output)

    def test_condense_line_breaks_multiple_newlines(self):
        test_input = "text\n\ntext"
        expected_output = "text \ntext"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)

    def test_condense_line_breaks_strips(self):
        test_input = "text\n"
        expected_output = "text"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)

    def test_condense_line_breaks_replaces_single_break_html_tag(self):
        test_input = "text<br>text"
        expected_output = "text\ntext"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)

    def test_condense_line_breaks_replaces_multiple_break_html_tags(self):
        test_input = "text<br><br>text"
        expected_output = "text \ntext"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)

    def test_condense_line_breaks_replaces_break_html_tags_with_bs4_slash(self):
        test_input = "text<br/>text"
        expected_output = "text\ntext"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)

    def test_condense_line_breaks_replaces_combo_break_html_tag_and_newline(self):
        test_input = "text<br>\ntext"
        expected_output = "text \ntext"
        self.assertEqual(adt.condense_line_breaks(test_input), expected_output)


class TestTransformationIntegrations(unittest.TestCase):

    def setUp(self):
        limited_html_transforms = [
            adt.to_limited_html,
        ]
        self.limited_html_transformer = adt.Transformer(limited_html_transforms, num_cores=4)

        text_transforms = [
            adt.to_text,
        ]
        self.text_transformer = adt.Transformer(text_transforms, num_cores=4)

        self.test_input = {'id': 0}
        self.expected_output = {'id': 0}

    def test_html_to_text(self):
        self.test_input['content'] = """
            <div class="field-item even" property="content:encoded"><div id="selectedWebpagePart" contenteditable="false"><div id="selectedWebpagePart" contenteditable="false"><div class="mainCol2Col selectedHighlight">
                   <div class="topleader">
                   <div class="vsp"> </div>
                    <div class="leader ad"><br></div></div><div class="mainContent">
                        
                        <div class="articleHtml">
            <div class="toolbar_ns" style="float:right;margin-top:-3px">
            <table><tbody></tbody></table></div>    
            <script>
            <!--//--><![CDATA[// ><!--
             function createToolbar() {	 
                if ('Antidepressants') {
                    var st=readCookie("SAVVYTOPICS");if (!st || st.indexOf("|Antidepressants|")==-1) {
                        var desc=st?"Click here to add <i>Antidepressants to your list of topics.":"<strong>Stay up-to-date on the health topics that interest you.<br /><br />Click here to sign in or sign up for HealthSavvy, and add <i>Antidepressants to your list of topics.";
                        addToolbarButton("HealthSavvy", "tb_hsicon tool_sp", "#",  savvyClick, "HealthSavvy","hs_savvy_favorite",desc);}
                }
                addToolbarButton( "Send this Page","tb_mail tool_sp", "#", function(event) {emailPage(event);return false;}, "Send Page",null, "<strong>Send Using Facebook or Email.<br /><br />Click here to send this page using Facebook or email. You may add a personal message to the email.");
                addToolbarButton( "Print","tb_print tool_sp", "#", function(event) {printPage(event);return false;}, "Print Article",null, "Click here to print this page."); 	   
             }
             createToolbar();  
            
            //--><!]]>
            </script><h1>Antidepressants</h1>
                        <div id="pageOneHeader"><div>
            <h3>Antidepressants are medications primarily used for treating depression.</h3></div></div></div></div><div>
            <a name="chapter_0" href="http://depression.emedtv.com/undefined" id="chapter_0"></a><h2>What Are Antidepressants?</h2></div>
                            <div>
            Antidepressants are medications used to treat <a href="http://depression.emedtv.com/depression/depression.html" onmouseout="hideDescription(event);" onmouseover="showDescription(event, '/depression/depression.html', 'Depression causes unnecessary suffering for both people who have the illness and their families.', 'Depression')">depression</a>. Some of these medications&nbsp;are blue.</div>
            <div>&nbsp;</div>
            <div><em>(Click <a title="Antidepressant Uses" href="http://depression.emedtv.com/antidepressants/antidepressant-uses.html" onmouseover="showDescription(event, '/antidepressants/antidepressant-uses.html', 'Besides depression treatment, antidepressants are also approved for other uses.', 'Antidepressant Uses')" onmouseout="hideDescription(event);">Antidepressant Uses</a> for more information on what&nbsp;they are used for, including possible <a href="http://drugs.emedtv.com/medicine/off-label.html" onmouseout="hideDescription(event);" onmouseover="showDescription(event, 'http://drugs.emedtv.com/medicine/off-label.html', 'This eMedTV page defines an off-label use as one where a physician prescribes a medication to treat a condition, even though the FDA has not approved the medicine for that specific use.', 'Off-Label')">off-label</a> uses.)</em></div>
            <div>&nbsp;</div>
            <div>
            <a name="chapter_1" href="http://depression.emedtv.com/undefined" id="chapter_1"></a><h2>Types of Antidepressants</h2></div>
                            <div>
            There are several types of antidepressants available to treat depression.</div>
            <div>&nbsp;</div>
            </div></div></div></div>
        """

        self.expected_output['content'] = """Antidepressants. 
Antidepressants are medications primarily used for treating depression. 
What Are Antidepressants?. 
Antidepressants are medications used to treat depression. Some of these medications are blue. 
(Click Antidepressant Uses for more information on what they are used for, including possible off-label uses.) 
Types of Antidepressants. 
There are several types of antidepressants available to treat depression."""

        output = self.text_transformer.apply([self.test_input])

        self.assertEqual(output, [self.expected_output])
